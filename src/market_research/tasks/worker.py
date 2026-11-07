"""Durable queue supervisor; each calculation runs in an isolated process."""
import argparse
import logging
import os
import sys
import threading
import time

import market_research.config as C
from market_research import logs
from market_research.tasks.jobs import JOBS
from market_research.tasks.processes import exclusive, launch, contain_children, terminate_tree
from market_research.settings import Settings
from market_research.tasks.store import bind_attempt

LOG=logging.getLogger(__name__)


def _prune_stale_windows():
    """清理与当前抓取窗口不符的重复指数池缓存（监督进程启动时执行一次）。

    显式自定义窗口会在 DuckDB 留下一次性窗口的整份指数缓存；它们可随时
    重抓、也不会被同步进程刷新，滞留只占空间。当前窗口的缓存永远保留。
    """
    try:
        from market_research.data import storage
        end = C.END_DATE
        keep = [f"index_universe_{C.FETCH_START_DATE}"]
        if end:
            keep.append(f"index_universe_{C.FETCH_START_DATE}_to{end}")
        removed = storage.prune_universe_windows(keep)
        if removed:
            LOG.info("清理重复窗口的指数池缓存：%s", ", ".join(removed))
    except Exception:
        LOG.warning("重复窗口缓存清理失败（不影响计算）", exc_info=True)


def execute(jid,token):
    job=JOBS.get(jid)
    if not job or job['token']!=token or job['status']!='running':
        return
    contain_children()
    from market_research.resources import apply_memory_limit, job_memory_mb
    apply_memory_limit()          # POSIX 在此生效；Windows 由 Job Object 限制
    done=threading.Event()
    def heartbeat():
        while not done.wait(5):
            try:
                if not JOBS.heartbeat(jid,token):
                    os._exit(125)
                if time.time()-job['started']>Settings.load().job_timeout:
                    JOBS.fail(jid,'计算超时，进程已终止',token=token)
                    os._exit(124)
            except Exception:
                LOG.exception('任务心跳失败')
    threading.Thread(target=heartbeat,name='job-heartbeat',daemon=True).start()
    try:
        with bind_attempt(token):
            from market_research.services.analysis import execute as calculate
            JOBS.finish(jid,calculate(job['operation'],job['arguments'],jid))
    except MemoryError:
        LOG.exception('任务超出内存上限 %s',jid)
        JOBS.fail(jid,f'计算超出内存上限（FIL_JOB_MEMORY_MB={job_memory_mb()}MB），'
                      '请减小窗口/模型数或提高上限后重试',token=token)
    except Exception as error:
        LOG.exception('任务失败 %s',jid)
        JOBS.fail(jid,f'{type(error).__name__}: {str(error)[:400]}',token=token)
    finally:
        done.set()


def supervise(once=False):
    with exclusive('analysis-worker.lock') as acquired:
        if not acquired:
            return
        JOBS.migrate()
        _prune_stale_windows()
        C.DATA_DIR.joinpath('worker.pid').write_text(str(os.getpid()),encoding='ascii')
        stop_path=C.DATA_DIR/'worker.stop'
        stop_path.unlink(missing_ok=True)
        children={}
        sync_child=None
        next_sync=0
        try:
            while not stop_path.exists():
                for jid,(child,job) in list(children.items()):
                    state=JOBS.public(jid)
                    timed_out=time.time()-job['started']>Settings.load().job_timeout
                    if child.poll() is None and (timed_out or not state or state['status']=='error'):
                        terminate_tree(child);child.wait(timeout=10)
                        if timed_out:
                            JOBS.fail(jid,'计算超时，进程已终止',token=job['token'])
                    if child.poll() is not None:
                        if os.name!='nt':
                            terminate_tree(child)
                        JOBS.retry_crashed(jid,job['token'],f'计算进程退出（{child.returncode}），将按策略重试')
                        del children[jid]
                while len(children)<Settings.load().max_jobs:
                    job=JOBS.claim()
                    if not job:
                        break
                    try:
                        child=launch('market_research.tasks.worker','--execute',job['id'],'--token',job['token'],log_name=f"job-{job['id']}.log")
                        children[job['id']]=(child,job)
                    except Exception as error:
                        JOBS.retry_crashed(job['id'],job['token'],str(error))
                        break
                if once and not children:
                    break
                if not once and os.environ.get('FIL_SYNC_ENABLED','1')!='0':
                    if sync_child and sync_child.poll() is not None:
                        sync_child=None;next_sync=time.time()+30
                    if sync_child is None and time.time()>=next_sync:
                        sync_child=launch('market_research.tasks.sync','--once',log_name='sync-process.log')
                time.sleep(.5)
        finally:
            # Claimed tasks remain durable; expired leases are recovered by the
            # next supervisor. Never leave an old child writing as a new attempt.
            for child,job in children.values():
                if child.poll() is None:
                    terminate_tree(child);child.wait(timeout=10)
                JOBS.retry_crashed(job['id'],job['token'],'工作进程停止，等待恢复')
            if sync_child and sync_child.poll() is None:
                sync_child.terminate();sync_child.wait(timeout=10)
            stop_path.unlink(missing_ok=True)
            C.DATA_DIR.joinpath('worker.pid').unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true',help='处理当前分析队列后退出，不启动同步调度')
    parser.add_argument('--stop',action='store_true',help='请求停止当前监督进程')
    parser.add_argument('--execute',help=argparse.SUPPRESS)
    parser.add_argument('--token',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.execute:
        # 任务子进程的 stdout/stderr 已被 supervisor 重定向到 job-<id>.log，
        # 保持流式写入即可（该文件随任务结束而停止增长，由保留期清理）。
        logging.basicConfig(level=logs.level(),
                            format='%(asctime)s %(levelname)s %(name)s %(message)s')
    else:
        logs.configure('worker')
        if sys.stderr.isatty():
            logging.getLogger().addHandler(logging.StreamHandler())
    if args.stop:
        C.DATA_DIR.mkdir(parents=True,exist_ok=True)
        (C.DATA_DIR/'worker.stop').touch()
    elif args.execute:
        execute(args.execute,args.token)
    else:
        supervise(args.once)


if __name__=='__main__':
    main()
