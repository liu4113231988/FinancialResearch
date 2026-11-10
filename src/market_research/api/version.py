"""协议版本单一来源：服务版本、对外 API 版本与兼容路径下线策略。"""
from datetime import date, datetime, timezone
from email.utils import formatdate
import os

SERVICE_VERSION = "0.3.0"
API_VERSION = "1"
API_PREFIX = "/api"
VERSIONED_PREFIX = "/api/v1"
LEGACY_REMOVE_AFTER = "2027-04-01"
LEGACY_SUNSET_HTTP_DATE = formatdate(
    datetime.fromisoformat(LEGACY_REMOVE_AFTER)
    .replace(tzinfo=timezone.utc).timestamp(), usegmt=True)


def legacy_sunset() -> str:
    """可由运维覆盖的下线 HTTP-date；默认使用单一来源的计划日期。"""
    return os.environ.get("FIL_API_SUNSET", "").strip() or LEGACY_SUNSET_HTTP_DATE


def legacy_enabled() -> bool:
    """兼容路径开关：auto 在计划移除日当天自动关闭，也可显式 0/1 覆盖。"""
    flag = os.environ.get("FIL_API_LEGACY", "auto").strip().lower()
    if flag in ("0", "false", "off", "disabled"):
        return False
    if flag in ("1", "true", "on", "enabled"):
        return True
    return date.today() < date.fromisoformat(LEGACY_REMOVE_AFTER)
