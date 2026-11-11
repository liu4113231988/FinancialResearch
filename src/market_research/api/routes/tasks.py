from fastapi import APIRouter
from fastapi.responses import JSONResponse
import market_research.services.analysis as services
from market_research.api.schemas import JobResponse, ResultResponse

router=APIRouter()


@router.get('/job/{jid}',response_model=JobResponse)
def status(jid:str):
    job=services.JOBS.public(jid)
    return job if job else JSONResponse({'ok':False,'error':'任务不存在或已过期'},status_code=404)

@router.get('/result/{jid}',response_model=ResultResponse)
def result(jid:str):
    job=services.JOBS.get(jid)
    if not job:
        return JSONResponse({'ok':False,'error':'任务不存在'},status_code=404)
    if job['status']=='error':
        # 用户取消 / 排队超时不是计算故障，不能一律 500。给出 reason 并把
        # 取消单独降到 409，调用方可以区分"主动放弃"与"真实异常"。
        # P2-42：优先用终态 stage（机器可读、写入后不再变化）分类，错误文案
        # 前缀只作兜底 —— worker 端改文案不得改变这里的分类结果。
        text=str(job.get('error') or '')
        stage=str(job.get('stage') or '')
        reason=('cancelled' if stage=='已取消' or text.startswith('任务已取消')
                else 'queue_timeout' if stage=='排队超时' or '排队超时' in text
                else 'failed')
        return JSONResponse({'ok':False,'error':job['error'],'status':'error','reason':reason},
                            status_code=409 if reason=='cancelled' else 500)
    if job['status']!='done':
        return JSONResponse({'ok':False,'error':'尚未完成','status':job['status']},status_code=202)
    payload = job['result'] or {}
    # P2-31：任务成功 ≠ 结果完整；把完整性契约一并返回，调用方可区分部分成功
    return {'ok': True, 'status': payload.get('status', 'ok'),
            'completeness': payload.get('completeness') or {},
            'result': payload}


@router.post('/job/{jid}/cancel')
def cancel(jid:str):
    return {'ok':services.JOBS.cancel(jid)}
