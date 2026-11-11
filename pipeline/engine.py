"""管线引擎：负责"把过滤器串起来并按配置驱动执行"。

引擎本身不含任何业务逻辑，它只做四件事：
    1. **自动发现** ``filters/`` 目录下的全部过滤器并注册（新增过滤器无需改引擎）；
    2. 按配置顺序逐级执行，每级独立计时、独立捕获异常；
    3. 记录每级的行数进出与耗时，产出可写入报告的执行轨迹（trace）；
    4. 提供 ``skip`` / ``abort`` 两种异常隔离策略。

「异常隔离」是本文档中可用性质量属性的实现基础：某一级因为脏数据失败时，
引擎记录该级状态并继续把数据交给下游，而不是让整条管线崩溃。
"""

from __future__ import annotations

import importlib
import pkgutil
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .base import Filter, FilterContext, FilterError

# ---------------------------------------------------------------------------
# 注册表：过滤器通过 ``@register`` 装饰器自注册，引擎按名字实例化
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, type[Filter]] = {}
_discovered = False


def register(cls: type[Filter]) -> type[Filter]:
    """类装饰器：把过滤器登记进注册表。"""
    if not getattr(cls, "name", ""):
        raise ValueError(f"{cls.__name__} 缺少 name 属性")
    _REGISTRY[cls.name] = cls
    return cls


def discover_filters() -> dict[str, type[Filter]]:
    """扫描 ``filters`` 包，导入其中全部模块以触发自注册。

    这就是"新增一个过滤器只需新增一个文件"的实现依据：
    引擎不维护硬编码列表，扩展点是目录本身。
    """
    global _discovered
    if _discovered:
        return _REGISTRY

    import filters as filters_pkg  # 延迟导入，避免循环依赖

    for module in pkgutil.iter_modules(filters_pkg.__path__):
        if module.name.startswith("_"):
            continue
        importlib.import_module(f"filters.{module.name}")
    _discovered = True
    return _REGISTRY


def available_filters() -> list[str]:
    return sorted(discover_filters().keys())


def create_filter(name: str, config: dict[str, Any] | None = None) -> Filter:
    registry = discover_filters()
    if name not in registry:
        raise FilterError(
            f"未知过滤器 {name!r}，当前已注册：{', '.join(sorted(registry))}"
        )
    return registry[name](config)


# ---------------------------------------------------------------------------
# 执行轨迹
# ---------------------------------------------------------------------------
@dataclass
class StageTrace:
    """单个过滤器的一次执行记录，是质量属性实测数据的来源。"""

    index: int
    total: int
    name: str
    label: str
    rows_in: int = 0
    rows_out: int = 0
    elapsed_ms: float = 0.0
    status: str = "PENDING"  # OK / ERROR / SKIPPED
    note: str = ""
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineResult:
    """一次完整管线运行的结果。"""

    context: FilterContext
    stages: list[StageTrace] = field(default_factory=list)
    total_elapsed_ms: float = 0.0
    status: str = "OK"

    @property
    def failed_stages(self) -> list[StageTrace]:
        return [s for s in self.stages if s.status != "OK"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total_elapsed_ms": round(self.total_elapsed_ms, 3),
            "row_count": self.context.row_count,
            "stages": [s.as_dict() for s in self.stages],
            "failed_stages": [s.name for s in self.failed_stages],
            "meta": self.context.meta,
        }


#: 每级执行完成后的观察者回调，用于 CLI 实时打印 / Web 进度推送
Observer = Callable[[StageTrace], None]


class Pipeline:
    """按配置顺序串接过滤器，逐级推送数据。

    Args:
        stages: ``[(过滤器名, 配置字典), ...]``，顺序即数据流方向。
        on_error: 单级失败后的策略。
            - ``skip``: 记录错误、跳过该级，数据原样交给下游（默认，演示可用性）
            - ``abort``: 立即中止整条管线（关键级别如数据加载适用）
    """

    def __init__(
        self,
        stages: list[tuple[str, dict[str, Any]]],
        on_error: str = "skip",
    ) -> None:
        if on_error not in {"skip", "abort"}:
            raise ValueError("on_error 只能是 'skip' 或 'abort'")
        self.stages = stages
        self.on_error = on_error

    def build(self) -> list[Filter]:
        """按配置实例化全部过滤器（触发各自的 setup 钩子）。

        在正式跑数据之前就把所有过滤器构造出来，可以尽早暴露配置错误，
        避免"跑到第五级才发现名字写错"。
        """
        instances: list[Filter] = []
        for name, config in self.stages:
            instance = create_filter(name, config)
            instance.setup()
            instances.append(instance)
        return instances

    def run(self, ctx: FilterContext | None = None, observer: Observer | None = None) -> PipelineResult:
        ctx = ctx or FilterContext()
        instances = self.build()
        total = len(instances)
        traces: list[StageTrace] = []

        t_pipeline_start = time.perf_counter()

        for i, instance in enumerate(instances, start=1):
            trace = StageTrace(
                index=i,
                total=total,
                name=instance.name,
                label=instance.label or instance.name,
                rows_in=ctx.row_count,
            )

            t0 = time.perf_counter()
            try:
                result = instance.process(ctx)
                # 允许过滤器返回新对象，但常规实现直接返回同一个 ctx
                if result is not None:
                    ctx = result
                trace.status = "OK"
            except FilterError as exc:
                trace.status = "ERROR"
                trace.error = str(exc)
                trace.note = "可预期错误，已隔离"
                ctx.warn(instance.name, f"过滤器失败：{exc}")
            except Exception as exc:  # noqa: BLE001 - 隔离一切故障，保证演示不中断
                trace.status = "ERROR"
                trace.error = f"{type(exc).__name__}: {exc}"
                trace.note = "未预期错误，已隔离"
                ctx.warn(instance.name, f"过滤器异常：{trace.error}")
            finally:
                trace.elapsed_ms = (time.perf_counter() - t0) * 1000
                trace.rows_out = ctx.row_count
                traces.append(trace)

            if observer:
                observer(trace)

            if trace.status == "ERROR" and self.on_error == "abort":
                for rest in instances[i:]:
                    traces.append(
                        StageTrace(
                            index=traces[-1].index + 1,
                            total=total,
                            name=rest.name,
                            label=rest.label or rest.name,
                            rows_in=ctx.row_count,
                            rows_out=ctx.row_count,
                            status="SKIPPED",
                            note="上游中止，未执行",
                        )
                    )
                    if observer:
                        observer(traces[-1])
                break

        result = PipelineResult(
            context=ctx,
            stages=traces,
            total_elapsed_ms=(time.perf_counter() - t_pipeline_start) * 1000,
            status="OK" if not any(t.status == "ERROR" for t in traces) else "DEGRADED",
        )
        ctx.meta["pipeline_status"] = result.status
        ctx.meta["total_elapsed_ms"] = round(result.total_elapsed_ms, 3)
        return result
