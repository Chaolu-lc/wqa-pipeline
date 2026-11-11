"""过滤器模板（文件名以下划线开头，引擎不会自动加载它）。

把本文件复制为 ``f8_xxx.py``，修改 ``name`` 与 ``label``，实现 ``process``，
再到 ``config/pipeline.yaml`` 的 ``stages`` 里加一行，即完成一次扩展——
不需要修改引擎，也不需要改动任何已有过滤器。

注意：文件名必须以字母开头（``f8_``、``f9_`` …），下划线开头的文件会被跳过。
"""

from __future__ import annotations

from pipeline.base import Filter, FilterContext
from pipeline.engine import register


@register
class SampleFilter(Filter):
    """在整条管线末端追加自定义处理逻辑。"""

    name = "sample"
    label = "示例过滤器"

    def process(self, ctx: FilterContext) -> FilterContext:
        # 从 ctx.records 读取上游产出，加工后仍写回 ctx
        # 约定：不修改自己不需要的字段，出错时抛 FilterError 交给引擎隔离
        ctx.info(self.name, f"收到 {ctx.row_count} 条记录")
        return ctx
