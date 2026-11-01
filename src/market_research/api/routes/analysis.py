import threading

from fastapi import APIRouter
from fastapi.responses import JSONResponse
import market_research.config as C
import market_research.data.marketdata as D
from market_research.validation.window import parse_window
import market_research.services.analysis as services
from market_research.api.schemas import AnalysisRequest, RefitRequest, BacktestRequest, SubmissionResponse

router=APIRouter()

# /api/corr 会在请求线程内 load_panel（首次可能要抓 40+ 个指数），
# 绕过任务队列且无法取消。用非阻塞锁把并发限制为 1，避免把 DuckDB
# 和 akshare 打满；第二个请求直接 429，由前端提示稍后重试。
_CORR_LOCK = threading.Lock()


@router.post('/api/analyze',response_model=SubmissionResponse,response_model_exclude_none=True)
def analyze(payload:AnalysisRequest):
    return services.submit('analyze',payload.model_dump())


@router.post('/api/refit',response_model=SubmissionResponse,response_model_exclude_none=True)
def refit(payload:RefitRequest):
    return services.submit('refit',payload.model_dump())


@router.post('/api/backtest',response_model=SubmissionResponse,response_model_exclude_none=True)
def backtest(payload:BacktestRequest):
    return services.submit('backtest',payload.model_dump())


@router.get('/api/name/{code}')
def name(code:str):
    if len(code)!=6 or not code.isdigit():
        raise ValueError('请输入六位基金代码')
    try:
        from market_research.funds.analysis import _guess_name
        result=_guess_name(code)
        return {'code':code,'name':result or code,'found':result is not None}
    except Exception as error:
        return {'code':code,'name':code,'found':False,'error':str(error)[:80]}


@router.get('/api/corr')
def correlation(code:str,win:int=120,start:str|None=None,end:str|None=None):
    if len(code)!=6 or not code.isdigit():
        raise ValueError('请输入六位基金代码')
    start,end=parse_window(start,end)
    if not _CORR_LOCK.acquire(blocking=False):
        return JSONResponse({'error':'相关系数正在计算，请稍后重试'},status_code=429)
    try:
        panel=D.load_panel(code,start=start or C.START_DATE,end=end,verbose=False)
        from market_research.funds.payloads import correlation_block
        result=correlation_block(panel,panel['__FUND__'],roll_n=max(20,min(win,750)))
        return {'ok':True,'code':code,'correlation':result}
    except Exception as error:
        return JSONResponse({'error':f'{type(error).__name__}: {str(error)[:200]}'},status_code=500)
    finally:
        _CORR_LOCK.release()
