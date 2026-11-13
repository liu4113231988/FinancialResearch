"""FastAPI composition root. Start with uv run market-research --port 8877."""
from contextlib import asynccontextmanager
import argparse
from fnmatch import fnmatchcase
import json
import logging
import os
from pathlib import Path
from importlib.resources import files

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import market_research.config as C
from market_research import logs
from market_research.errors import (JobsBusy, StorageBusy, NavBasisUnavailable,
                                    ProviderContractError)
from market_research.security import AccessControl, mode_description
from market_research.tasks.jobs import JOBS
from market_research.tasks.processes import launch
from market_research.settings import Settings
import market_research.api.routes.analysis as analysis
import market_research.api.routes.market as market
import market_research.api.routes.meta as meta
import market_research.api.routes.sync as sync
import market_research.api.routes.tasks as tasks
from market_research.api.response_meta import enrich as enrich_response_meta
from market_research.api.schemas import ErrorResponse
from market_research.api.version import (API_PREFIX, API_VERSION, SERVICE_VERSION,
                                         VERSIONED_PREFIX, legacy_enabled,
                                         legacy_sunset)

LOG=logging.getLogger(__name__)
RESPONSE_LOG=logging.getLogger('market_research.api.response')
STATIC=Path(str(files('market_research.api')/'static'))


class BodyLimit:
    """Bound JSON request bodies even when no Content-Length is supplied."""
    def __init__(self,app,limit=8*1024*1024):
        self.app,self.limit=app,limit

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        messages=[]
        size=0
        while True:
            message=await receive()
            messages.append(message)
            size+=len(message.get('body',b''))
            if size>self.limit:
                # 超限后请求体未读完，keep-alive 连接上继续读会协议错乱：
                # 明确要求服务端在响应后关闭连接（P2-42）。
                return await JSONResponse({'ok':False,'error':'请求体超过 8 MB','field':'body'},
                                          status_code=413,
                                          headers={'connection':'close'})(scope,receive,send)
            if message['type']=='http.disconnect' or not message.get('more_body',False):
                break
        async def replay():
            return messages.pop(0) if messages else await receive()
        await self.app(scope,replay,send)


class VersionHeader:
    """标记协议版本；兼容 /api 路径显式提示弃用与 v1 后继地址（P3-H）。"""
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        path=scope.get('path','')
        legacy=(path.startswith(API_PREFIX + '/')
                and not path.startswith(VERSIONED_PREFIX + '/')
                and legacy_enabled())
        successor=VERSIONED_PREFIX + path[len(API_PREFIX):]
        async def wrapped(message):
            if message['type']=='http.response.start':
                headers=list(message.get('headers') or [])
                headers.append((b'x-api-version',API_VERSION.encode('ascii')))
                if legacy:
                    headers.append((b'deprecation',b'true'))
                    headers.append((b'link',f'<{successor}>; rel="successor-version"'.encode('ascii')))
                    headers.append((b'sunset',
                                    legacy_sunset().encode('ascii','ignore')))
                message={**message,'headers':headers}
            await send(message)
        await self.app(scope,receive,wrapped)


class ApiResponseLog:
    """Opt-in logging for final JSON API responses."""

    def __init__(self):
        setting = os.environ.get('FIL_API_RESPONSE_LOG', '').strip()
        self.all_paths = setting.lower() == 'all'
        paths = (part.strip() for part in setting.split(','))
        self.patterns = tuple(path for path in paths if path.startswith(API_PREFIX + '/'))
        self.limit = logs.api_response_max_bytes()

    async def write(self, scope, status, body):
        if not (self.all_paths or self.patterns) or not RESPONSE_LOG.isEnabledFor(logging.INFO):
            return
        path = scope['path']
        if not path.startswith(API_PREFIX + '/') or not body:
            return
        if not self.all_paths and not any(fnmatchcase(path, pattern) for pattern in self.patterns):
            return
        truncated = 0 < self.limit < len(body)
        text = (body[:self.limit] if truncated else body).decode('utf-8', errors='ignore')
        text = text.replace('\r', r'\r').replace('\n', r'\n')
        await run_in_threadpool(
            RESPONSE_LOG.info, '%s %r status=%d bytes=%d body=%s%s',
            scope['method'], path, status, len(body), text,
            ' [truncated]' if truncated else '',
        )


class MetaEnvelope:
    """给所有 API JSON 响应补齐 meta 信封，包括异常和中间件错误（P2-36）。"""
    def __init__(self,app,response_log):
        self.app=app
        self.response_log=response_log

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not scope.get('path','').startswith(API_PREFIX):
            return await self.app(scope,receive,send)
        messages=[]
        async def capture(message):
            messages.append(message)
        await self.app(scope,receive,capture)
        start=next((m for m in messages if m['type']=='http.response.start'),None)
        headers=(start or {}).get('headers',[])
        content_type=next((value.decode('latin-1').lower() for key,value in headers
                           if key.lower()==b'content-type'),'')
        chunks=[m.get('body',b'') for m in messages if m['type']=='http.response.body']
        if not start or 'application/json' not in content_type or not any(chunks):
            for message in messages:
                await send(message)
            return
        try:
            payload=json.loads(b''.join(chunks).decode('utf-8'))
            enriched=enrich_response_meta(payload)
            body=json.dumps(enriched,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        except (UnicodeDecodeError,ValueError,TypeError):
            for message in messages:
                await send(message)
            return
        updated=[(key,value) for key,value in headers
                 if key.lower()!=b'content-length']
        updated.append((b'content-length',str(len(body)).encode('ascii')))
        sent_body=False
        for message in messages:
            if message['type']=='http.response.start':
                await send({**message,'headers':updated})
            elif message['type']=='http.response.body':
                if not sent_body:
                    await send({**message,'body':body,'more_body':False})
                    sent_body=True
            else:
                await send(message)
        await self.response_log.write(scope,start['status'],body)


_CACHEABLE_SUFFIXES=('.ico','.svg','.png','.webmanifest')


class IconCache:
    """图标与 manifest 允许浏览器缓存；内容变化时仍由 ETag/Last-Modified 校验。"""
    def __init__(self,app,seconds=86400):
        self.app=app
        self.seconds=seconds

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not scope['path'].lower().endswith(_CACHEABLE_SUFFIXES):
            return await self.app(scope,receive,send)
        async def wrapped(message):
            if message['type']=='http.response.start':
                headers=list(message.get('headers') or [])
                if not any(key.lower()==b'cache-control' for key,_ in headers):
                    headers.append((b'cache-control',
                                    f'public, max-age={self.seconds}'.encode('ascii')))
                    message={**message,'headers':headers}
            await send(message)
        await self.app(scope,receive,wrapped)



@asynccontextmanager
async def lifespan(app):
    settings=Settings.load().validate()
    JOBS.migrate()
    if settings.auto_services:
        # The independent singleton supervisor survives API restart. Every
        # analysis has its own process; duplicate supervisors exit on file lock.
        launch('market_research.tasks.worker',log_name='worker-process.log')
    yield


def create_app():
    app=FastAPI(title='Market Research API',version=SERVICE_VERSION,lifespan=lifespan,
                responses={400:{'model':ErrorResponse},429:{'model':ErrorResponse}})
    app.add_middleware(VersionHeader)
    app.add_middleware(BodyLimit)
    app.add_middleware(IconCache)
    # P2-24：默认单用户不启用认证；开启后用身份上下文贯通调用链。
    app.add_middleware(AccessControl)
    LOG.info('访问控制：%s', mode_description())
    origins=[v.strip() for v in os.environ.get('FIL_CORS_ORIGINS','').split(',') if v.strip()]
    if origins:
        app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST'],allow_headers=['Content-Type','Authorization','X-API-Key'])
    # 最后注册成为最外层，确保所有 API JSON 结果（含认证/校验/限流错误）都有信封。
    response_log=ApiResponseLog()
    app.add_middleware(MetaEnvelope,response_log=response_log)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request:Request,error:RequestValidationError):
        issue=error.errors()[0]
        location=[str(x) for x in issue['loc'] if x not in ('body','query','path')]
        return JSONResponse({'ok':False,'error':issue['msg'].removeprefix('Value error, '),'field':'.'.join(location) or 'body'},status_code=400)

    @app.exception_handler(JobsBusy)
    async def busy(request:Request,error:JobsBusy):
        return JSONResponse({'ok':False,'error':str(error),'field':'concurrency'},status_code=429)

    @app.exception_handler(StorageBusy)
    async def storage_busy(request:Request,error:StorageBusy):
        # P2-30：存储锁超时是**暂时性**故障，必须回 503 + 可读原因，
        # 而不是被通用 500 吞掉；客户端可据此稍后重试。
        LOG.warning('存储繁忙 %s：%s',request.url.path,error)
        return JSONResponse({'ok':False,'error':str(error),'field':'storage',
                             'retry_after_s':int(C.STORAGE_LOCK_TIMEOUT)},status_code=503)

    @app.exception_handler(ValueError)
    async def invalid(request:Request,error:ValueError):
        # P2-42：NavBasisUnavailable / ProviderContractError 是 ValueError 子类，
        # 但它们是**服务端数据问题**（口径降级、供应商契约破坏），不是用户
        # 参数错误 —— 不能被这里伪装成 400 field=params；交给 500 并留日志。
        if isinstance(error,(NavBasisUnavailable,ProviderContractError)):
            LOG.error('数据契约失败 %s',request.url.path,exc_info=error)
            return JSONResponse({'ok':False,'error':str(error),'field':'data_source'},status_code=500)
        return JSONResponse({'ok':False,'error':str(error),'field':'params'},status_code=400)

    @app.exception_handler(Exception)
    async def unexpected(request:Request,error:Exception):
        LOG.error('请求处理失败 %s',request.url.path,exc_info=error)
        response=JSONResponse({'ok':False,'error':'服务处理失败，请查看运行日志后重试'},status_code=500)
        # ServerErrorMiddleware sends this response outside the user middleware.
        await response_log.write(request.scope,response.status_code,response.body)
        return response

    # 浏览器与 iOS 会直接请求根路径图标；显式路由保证类型与缓存头，
    # 也避免静态目录之外的默认图标请求落到 SPA 兜底。
    @app.get('/favicon.ico',include_in_schema=False)
    def favicon_ico():
        return FileResponse(STATIC/'favicon.ico',media_type='image/x-icon')

    @app.get('/favicon.svg',include_in_schema=False)
    def favicon_svg():
        return FileResponse(STATIC/'favicon.svg',media_type='image/svg+xml')

    @app.get('/apple-touch-icon.png',include_in_schema=False)
    def apple_touch_icon():
        return FileResponse(STATIC/'apple-touch-icon.png',media_type='image/png')

    @app.get('/site.webmanifest',include_in_schema=False)
    def web_manifest():
        return FileResponse(STATIC/'site.webmanifest',
                            media_type='application/manifest+json')

    @app.get('/',include_in_schema=False)
    def index():
        return FileResponse(STATIC/'index.html')

    # /api/v1 是正式契约路径；/api 在 legacy_enabled() 时挂同一批路由。
    # 到计划移除日或显式 FIL_API_LEGACY=0 后，只保留带版本前缀的契约。
    prefixes=(API_PREFIX,VERSIONED_PREFIX) if legacy_enabled() else (VERSIONED_PREFIX,)
    for prefix in prefixes:
        for module in (meta,analysis,market,sync,tasks):
            app.include_router(module.router,prefix=prefix)
    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    app.mount('/vendor',StaticFiles(directory=STATIC/'vendor'),name='vendor')
    return app


app=create_app()


def main():
    logs.configure('api')
    settings=Settings.load()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default=settings.host)
    parser.add_argument('--port',type=int,default=settings.port)
    parser.add_argument('--debug',action='store_true')
    args=parser.parse_args()
    import uvicorn
    console=logging.StreamHandler()
    RESPONSE_LOG.addHandler(console)
    try:
        uvicorn.run(app,host=args.host,port=args.port,log_level='debug' if args.debug else 'info')
    finally:
        RESPONSE_LOG.removeHandler(console)
        console.close()


if __name__=='__main__':
    main()
