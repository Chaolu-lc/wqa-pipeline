"""管道-过滤器（Pipe-Filter）架构的公共抽象层。

整条管线的数据流只有一条通道：一个 ``FilterContext`` 对象在上游到下游之间单向流动。
上游过滤器读取它、加工它，再原样交给下游；过滤器之间**不互相调用，也不知道彼此的存在**。

这个约束是管道-过滤器风格的核心：
    - 每个过滤器只关心"输入什么/输出什么"，不关心数据从哪来、要去哪；
    - 因此新增、替换、删减一个过滤器，都不会影响其他过滤器（可修改性的来源）；
    - 每个过滤器都可以脱离整条管线，用固定的输入单独测试（可测试性的来源）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class FilterError(Exception):
    """过滤器内部**可预期**的错误（脏数据、配置缺失、文件不存在等）。

    引擎会捕获它并交给异常隔离策略处理，用于演示"单级故障不拖垮整条管线"。
    非预期错误（代码 bug）同样会被隔离，但会在 trace 中标记为 ERROR 以便排查。
    """


@dataclass
class FilterContext:
    """流经整条管线的唯一数据载体。

    Attributes:
        records: 主数据，逐条记录的字典列表。所有过滤器都在这上面做读写。
        meta: 管线级元信息（数据来源、时间范围、标准版本等），供下游与报告使用。
        artifacts: 中间产物（统计结果、图表路径、告警列表），不参与逐条加工。
        events: 运行期事件日志，按过滤器分组记录，"发生了什么"的可追溯证据。
    """

    records: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    # ---- 事件记录：统一入口，保证各级日志格式一致，便于报告直接渲染 ----
    def log(self, level: str, source: str, message: str, **extra: Any) -> None:
        self.events.append({"level": level, "filter": source, "message": message, **extra})

    def info(self, source: str, message: str, **extra: Any) -> None:
        self.log("INFO", source, message, **extra)

    def warn(self, source: str, message: str, **extra: Any) -> None:
        self.log("WARN", source, message, **extra)

    @property
    def row_count(self) -> int:
        return len(self.records)


class Filter(ABC):
    """所有过滤器的抽象基类。

    子类只需要做两件事：
        1. 声明类属性 ``name`` / ``label``；
        2. 实现 ``process()`` —— 从 ctx 里取数据，加工后写回 ctx。

    新增一个过滤器 = 在 ``filters/`` 目录下新建一个文件 + 在配置里加一行。
    不需要修改引擎、不需要修改其他过滤器（对扩展开放，对修改关闭）。
    """

    #: 在配置文件中引用该过滤器时使用的唯一标识
    name: str = "filter"
    #: 用于日志与报告的中文名
    label: str = ""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})

    def setup(self) -> None:
        """可选的生命周期钩子：加载标准文件、创建临时资源等。

        默认什么都不做，绝大多数过滤器不需要覆写。
        """

    @abstractmethod
    def process(self, ctx: FilterContext) -> FilterContext:
        """处理数据并返回同一个 ctx（便于链式调用与统一计时）。"""
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<{type(self).__name__} name={self.name!r}>"
