import logging
import os
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import market_research.tasks.sync as sync
from market_research.api.schemas import SyncAssetRequest, SyncRetryRequest

router=APIRouter()
LOG = logging.getLogger(__name__)


@router.get('/sync')
def status():
    try:
        scheduled = sync.schedule_time().strftime('%H:%M')
    except ValueError as error:
        LOG.exception('同步调度配置无效')
        return JSONResponse(status_code=500, content={
            'ok': False, 'field': 'config', 'error': str(error)})
    return {'enabled':os.environ.get('FIL_SYNC_ENABLED','1')!='0','time':scheduled,
            'timezone':'Asia/Shanghai','assets':sync.assets()}


@router.post('/sync/assets')
def add(payload:SyncAssetRequest):
    key=sync.register(payload.kind,payload.code,payload.basis)
    return {'ok':True,'asset_key':key}


@router.post('/sync/retry')
def retry(payload:SyncRetryRequest):
    return {'ok':True,'queued':sync.request_sync(payload.asset_key)}
