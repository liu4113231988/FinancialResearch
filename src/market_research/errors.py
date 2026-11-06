class JobsBusy(Exception):
    """The durable queue has reached its configured capacity."""


class NavBasisUnavailable(ValueError):
    """总回报净值口径不可用。

    P1-20：累计净值相对总回报系统性低估，不能把退化结果当作 total 口径
    继续计算；默认直接失败，只有显式允许降级时才带标记返回累计净值。
    """


class CacheMiss(RuntimeError):
    """只读取数模式下所需缓存不存在。

    区分于"数据源抓取失败"：这里根本没发起抓取。请求线程内的轻量接口
    （如 /api/corr）用它在缓存未预热时立即失败，而不是替用户触发几十个
    指数的联网抓取 —— 那种抓取无法取消、也不受任务队列名额约束。
    """


class ProviderContractError(ValueError):
    """数据供应商返回的字段/结构不符合契约（P2-29）。

    公开抓取没有商业 SLA，字段改名、列缺失、返回空表都可能发生。这种
    情况必须显式失败并带出端点与期望字段，绝不能静默跳过或按错误口径
    继续计算。
    """


class StorageBusy(RuntimeError):
    """等待 DuckDB 文件锁超时（P2-30）。

    同步任务逐资产整表重写时，请求路径的读写会排队；等待超过
    FIL_STORAGE_LOCK_TIMEOUT 秒仍拿不到锁就显式失败，而不是无限期卡住
    且没有任何提示。
    """
