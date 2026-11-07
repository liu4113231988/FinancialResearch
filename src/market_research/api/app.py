"""FastAPI composition root. Start with uv run market-research --port 8877."""
from contextlib import asynccontextmanager
import argparse
import logging
import os
from pathlib import Path
from importlib.resources import files

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import market_research.config as C
from market_research import logs
from market_research.errors import JobsBusy, StorageBusy
from market_research.security import AccessControl, mode_description
from market_research.tasks.jobs import JOBS
from market_research.tasks.processes import launch
from market_research.settings import Settings
import market_research.api.routes.analysis as analysis
import market_research.api.routes.market as market
import market_research.api.routes.meta as meta
import market_research.api.routes.sync as sync
import market_research.api.routes.tasks as tasks
from market_research.api.schemas import ErrorResponse
from market_research.api.version import API_PREFIX, API_VERSION, SERVICE_VERSION, VERSIONED_PREFIX

LOG=logging.getLogger(__name__)
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
                return await JSONResponse({'ok':False,'error':'请求体超过 8 MB','field':'body'},status_code=413)(scope,receive,send)
            if message['type']=='http.disconnect' or not message.get('more_body',False):
                break
        async def replay():
            return messages.pop(0) if messages else await receive()
        await self.app(scope,replay,send)


class VersionHeader:
    """给所有 HTTP 响应标记协议版本，便于调用方判断契约（P3-H）。"""
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        async def wrapped(message):
            if message['type']=='http.response.start':
                headers=list(message.get('headers') or [])
                headers.append((b'x-api-version',API_VERSION.encode('ascii')))
                message={**message,'headers':headers}
            await send(message)
        await self.app(scope,receive,wrapped)


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
    JOBS.migrate()
    if Settings.load().auto_services:
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
        app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST'],allow_headers=['Content-Type'])

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
        return JSONResponse({'ok':False,'error':str(error),'field':'params'},status_code=400)

    @app.exception_handler(Exception)
    async def unexpected(request:Request,error:Exception):
        LOG.error('请求处理失败 %s',request.url.path,exc_info=error)
        return JSONResponse({'ok':False,'error':'服务处理失败，请查看运行日志后重试'},status_code=500)

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

    # /api/v1 是正式契约路径；/api 保留为兼容路径，两者挂同一批路由，
    # OpenAPI 同时列出，避免新旧调用方各自维护一套实现。
    for prefix in (API_PREFIX,VERSIONED_PREFIX):
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
    uvicorn.run(app,host=args.host,port=args.port,log_level='debug' if args.debug else 'info')


if __name__=='__main__':
    main()
