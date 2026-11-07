from fastapi import APIRouter
from fastapi.responses import JSONResponse
import market_research.config as C
import market_research.data.marketdata as D
from market_research.validation.window import parse_window
from market_research.errors import CacheMiss, JobsBusy, StorageBusy
import market_research.services.analysis as services
from market_research.api.schemas import AnalysisRequest, RefitRequest, BacktestRequest, HoldingsRequest, SubmissionResponse

router=APIRouter()

# 这两个端点跑在 URL 请求线程里，**不能**联网取数：
#   - /api/corr 首次取数要抓 40+ 个指数，几十秒起，请求线程里既不可取消也不占
#     任务队列名额；/api/name 的名单接口挂起过，会把请求线程卡死。
# 所以统一按"只读"调用：缓存已预热就直接算（毫秒级）；/api/corr 冷缓存时改
# 为提交一个 corr 队列任务（202 + job_id）—— 取数与计算都在队列里，可取消、
# 占名额；请求线程自始至终不联网。/api/name 未命中则回落"未查"状态。


@router.post('/analyze',response_model=SubmissionResponse,response_model_exclude_none=True)
def analyze(payload:AnalysisRequest):
    return services.submit('analyze',payload.model_dump())


@router.post('/refit',response_model=SubmissionResponse,response_model_exclude_none=True)
def refit(payload:RefitRequest):
    return services.submit('refit',payload.model_dump())


@router.post('/backtest',response_model=SubmissionResponse,response_model_exclude_none=True)
def backtest(payload:BacktestRequest):
    return services.submit('backtest',payload.model_dump())


@router.post('/holdings',response_model=SubmissionResponse,response_model_exclude_none=True)
def holdings(payload:HoldingsRequest):
    return services.submit('holdings',payload.model_dump())


@router.get('/name/{code}')
def name(code:str):
    if len(code)!=6 or not code.isdigit():
        raise ValueError('请输入六位基金代码')
    try:
        from market_research.funds.analysis import _cached_name
        cached,state=_cached_name(code)
        # found=True 查到名字；False 名录确实拉到过、没有这只基金（负缓存）；
        # None 本次没查。前端只在 False 时提示"代码可能有误"。
        return {'code':code,'name':cached or code,
                'found':{'hit':True,'absent':False}.get(state),
                'deferred':state=='unknown'}
    except Exception as error:
        return {'code':code,'name':code,'found':None,'deferred':True,
                'error':str(error)[:80]}


@router.get('/corr')
def correlation(code:str,win:int=120,start:str|None=None,end:str|None=None):
    if len(code)!=6 or not code.isdigit():
        raise ValueError('请输入六位基金代码')
    start,end=parse_window(start,end)
    try:
        panel=D.load_panel(code,start=start or C.START_DATE,end=end,
                           verbose=False,allow_fetch=False)
    except CacheMiss:
        # 冷缓存（P1-13）：不再 409 让用户"先刷新数据"，而是把取数交回队列 ——
        # 可取消、占计算名额，请求线程只读不联网。
        try:
            submission=services.submit('corr',{'code':code,'win':win,
                                               'start':start,'end':end})
        except JobsBusy as error:
            return JSONResponse({'error':str(error)},status_code=429)
        return JSONResponse(dict(submission),status_code=202)
    except StorageBusy:
        # P2-30：交给全局异常处理映射为 503，不要在这里退化成通用 500。
        raise
    except Exception as error:
        return JSONResponse({'error':f'{type(error).__name__}: {str(error)[:200]}'},status_code=500)
    try:
        from market_research.funds.payloads import correlation_block
        result=correlation_block(panel,panel['__FUND__'],roll_n=max(20,min(win,750)))
        return {'ok':True,'code':code,'correlation':result}
    except Exception as error:
        return JSONResponse({'error':f'{type(error).__name__}: {str(error)[:200]}'},status_code=500)
