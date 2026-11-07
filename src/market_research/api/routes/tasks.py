from fastapi import APIRouter
from fastapi.responses import JSONResponse
import market_research.services.analysis as services
from market_research.api.schemas import JobResponse, ResultResponse

router=APIRouter()


@router.get('/job/{jid}',response_model=JobResponse)
def status(jid:str):
    job=services.JOBS.public(jid)
    return job if job else JSONResponse({'error':'任务不存在或已过期'},status_code=404)

@router.get('/result/{jid}',response_model=ResultResponse)
def result(jid:str):
    job=services.JOBS.get(jid)
    if not job:
        return JSONResponse({'error':'任务不存在'},status_code=404)
    if job['status']=='error':
        return JSONResponse({'error':job['error']},status_code=500)
    if job['status']!='done':
        return JSONResponse({'error':'尚未完成','status':job['status']},status_code=202)
    payload = job['result'] or {}
    # P2-31：任务成功 ≠ 结果完整；把完整性契约一并返回，调用方可区分部分成功
    return {'ok': True, 'status': payload.get('status', 'ok'),
            'completeness': payload.get('completeness') or {},
            'result': payload}


@router.post('/job/{jid}/cancel')
def cancel(jid:str):
    return {'ok':services.JOBS.cancel(jid)}
