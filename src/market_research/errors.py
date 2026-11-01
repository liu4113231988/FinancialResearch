class JobsBusy(Exception):
    """The durable queue has reached its configured capacity."""


class CacheMiss(RuntimeError):
    """只读取数模式下所需缓存不存在。

    区分于"数据源抓取失败"：这里根本没发起抓取。请求线程内的轻量接口
    （如 /api/corr）用它在缓存未预热时立即失败，而不是替用户触发几十个
    指数的联网抓取 —— 那种抓取无法取消、也不受任务队列名额约束。
    """
