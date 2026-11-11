"""管道-过滤器架构的内核：抽象层、引擎、标准计算与图表渲染。"""

from .base import Filter, FilterContext, FilterError
from .engine import (
    Pipeline,
    PipelineResult,
    StageTrace,
    available_filters,
    create_filter,
    discover_filters,
    register,
)

__all__ = [
    "Filter",
    "FilterContext",
    "FilterError",
    "Pipeline",
    "PipelineResult",
    "StageTrace",
    "available_filters",
    "create_filter",
    "discover_filters",
    "register",
]
