from __future__ import annotations
import os, random, threading, time, contextlib
from typing import Optional
import pandas as pd

from market_research.data import provenance

_TLS = threading.local()

# 主动限流（P2-29）：公开抓取没有商业 SLA，必须自己控制请求速率，
# 避免并发 6 线程把对方站点打限。FIL_HTTP_MAX_RPS <= 0 表示关闭限流。
_RATE_LOCK = threading.Lock()
_LAST_CALL = [0.0]


def _max_rps() -> float:
    try:
        value = float(os.environ.get("FIL_HTTP_MAX_RPS", "8"))
    except (TypeError, ValueError):                        # noqa: BLE001
        return 8.0
    return max(value, 0.0)


def rate_limit(deadline: Optional[float] = None) -> None:
    """全进程共享的最小请求间隔；单资产 deadline 到期立即停止等待。"""
    rps = _max_rps()
    if rps <= 0:
        return
    interval = 1.0 / rps
    with _RATE_LOCK:
        delay = _LAST_CALL[0] + interval - time.monotonic()
        if deadline is not None and delay > 0:
            delay = min(delay, max(0.0, deadline - time.monotonic()))
        if delay > 0:
            time.sleep(delay)
        _LAST_CALL[0] = time.monotonic()
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("单资产请求总截止时间已到")


def _non_retryable(exc: Exception) -> bool:
    """编程错误不重试；JSON 截断仍属于可重试的网络问题。"""
    from json import JSONDecodeError
    from market_research.errors import ProviderContractError
    if isinstance(exc, ProviderContractError):
        return True
    if isinstance(exc, JSONDecodeError):
        return False
    return isinstance(exc, (KeyError, TypeError, AttributeError, ValueError))


def _default_http_timeout() -> float:
    """抓取超时（秒）。FIL_HTTP_TIMEOUT=0 表示不限制。"""
    try:
        v = float(os.environ.get("FIL_HTTP_TIMEOUT", "30"))
    except (TypeError, ValueError):                        # noqa: BLE001
        return 30.0
    return v if v > 0 else 0.0


def _thread_deadline() -> Optional[float]:
    return getattr(_TLS, "deadline", None)


def _thread_timeout() -> Optional[float]:
    sec = getattr(_TLS, "timeout", None)
    deadline = _thread_deadline()
    if deadline is None:
        return sec
    remaining = max(0.001, deadline - time.monotonic())
    return min(sec, remaining) if sec and sec > 0 else remaining


def _apply_request_timeout(kwargs: dict) -> dict:
    """只给当前线程的抓取请求补 timeout，不碰进程级 socket 默认值。"""
    sec = _thread_timeout()
    if sec and kwargs.get("timeout") is None:
        kwargs["timeout"] = sec
    return kwargs


def _install_requests_timeout() -> None:
    """把超时下沉到请求层（P2-37）。

    AkShare 的请求没有暴露 timeout 参数，但 requests 是统一入口；给
    Session.request 注入线程级 timeout，比 socket.setdefaulttimeout 更窄：
    只影响正在抓取的线程，不会误伤 Flask 的监听/连接 socket。
    """
    try:
        import requests
    except Exception:                                      # noqa: BLE001
        return
    current = requests.Session.request
    if getattr(current, "_fil_timeout_patched", False):
        return

    def request(self, method, url, **kwargs):
        return current(self, method, url, **_apply_request_timeout(kwargs))

    request._fil_timeout_patched = True
    requests.Session.request = request


_install_requests_timeout()


@contextlib.contextmanager
def http_timeout(seconds: Optional[float] = None):
    """仅当前线程生效的抓取超时（P2-37）。

    ★ 不再使用 socket.setdefaulttimeout：它是进程全局的，会波及 Flask
      监听 socket 与其它线程；这里改为线程局部值 + requests 请求级 timeout。
    """
    sec = _default_http_timeout() if seconds is None else seconds
    deadline = _thread_deadline()
    if sec and sec > 0 and deadline is not None:
        sec = min(sec, max(0.001, deadline - time.monotonic()))
    previous = getattr(_TLS, "timeout", None)
    _TLS.timeout = sec if sec and sec > 0 else None
    try:
        yield
    finally:
        _TLS.timeout = previous


@contextlib.contextmanager
def http_deadline(deadline: Optional[float]):
    """仅当前同步线程生效的请求总截止时间（monotonic 秒）。"""
    previous = _thread_deadline()
    _TLS.deadline = deadline
    try:
        yield
    finally:
        _TLS.deadline = previous


def _retry(fn, tries: int = 4, wait: float = 1.2, source: str = "",
           deadline: Optional[float] = None, **kw):
    """统一的重试 / 退避 / 限流 / 溯源入口（P2-29）。

    只重试可恢复的网络类异常；字段缺失、类型错误等编程错误立即抛出，
    避免把一个问题重试四次后仍得到同样的错误结果。deadline 是单资产同步
    的总预算，耗尽后不再发起下一轮请求。
    """
    last = None
    limit = deadline if deadline is not None else _thread_deadline()
    tries = max(int(tries), 1)
    for i in range(tries):
        if limit is not None and time.monotonic() >= limit:
            last = TimeoutError("单资产请求总截止时间已到")
            break
        try:
            rate_limit(deadline=limit)
        except TimeoutError as exc:
            last = exc
            break
        # ★ 超时只在当前线程的抓取请求上生效（P2-37）：注入 requests 的
        #   timeout，不动进程级 socket 默认值。
        with http_timeout():
            try:
                result = fn(**kw)
                provenance.record(source, ok=True)
                return result
            except Exception as e:      # noqa: BLE001
                last = e
                if _non_retryable(e):
                    provenance.record(source, ok=False, error=_describe(e))
                    raise
        if i < tries - 1:
            delay = min(wait * (2 ** i), 30.0) + random.uniform(0, min(1.0, wait))
            if limit is not None:
                delay = min(delay, max(0.0, limit - time.monotonic()))
            time.sleep(delay)
    provenance.record(source, ok=False, degraded=True, error=_describe(last))
    raise last or RuntimeError("数据源请求失败")


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:180]}"


def _clean_series(s: pd.Series, start: Optional[str] = None) -> pd.Series:
    """通用清洗：剔除非正值、按日期去重、截断起点。"""
    s = s[s > 0]
    s = s[~s.index.duplicated(keep="last")]
    if start:
        s = s[s.index >= pd.Timestamp(start)]
    return s
