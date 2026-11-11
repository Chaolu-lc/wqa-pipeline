"""阶段 5 · 水质评价过滤器。

调用 ``pipeline.standards`` 中的纯计算函数，对每条记录给出：
单因子类别、综合水质类别、内梅罗污染指数、超标项清单。

标准限值在 ``setup()`` 中一次性加载——这是生命周期钩子的典型用途：
把"读配置"这类一次性成本前置，避免在逐条处理的循环里重复读文件。
"""

from __future__ import annotations

from collections import defaultdict

from pipeline.base import Filter, FilterContext, FilterError
from pipeline.engine import register
from pipeline.standards import assess, load_standard


@register
class IndexFilter(Filter):
    """单因子评价 + 内梅罗综合污染指数。"""

    name = "index"
    label = "水质评价"

    def setup(self) -> None:
        path = self.config.get("standard", "config/water_quality_standard.yaml")
        self.standard = load_standard(path)

    def process(self, ctx: FilterContext) -> FilterContext:
        if not ctx.records:
            raise FilterError("上游未提供任何数据")

        indicators: list[str] = list(self.standard["indicators"].keys())
        ranges: dict = self.standard.get("compliance_ranges", {})
        distribution: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        unassessed = 0

        for row in ctx.records:
            result = assess(row, self.standard)
            row["wq_class"] = result["wq_class"]
            row["pollution_index"] = result["pollution_index"]
            row["exceed_items"] = ",".join(result["exceed_items"])

            for field in indicators:
                row[f"class_{field}"] = result["field_classes"].get(field)

            ph = row.get("ph")
            if ph is not None and "ph" in ranges:
                low, high = ranges["ph"]["range"]
                row["ph_compliant"] = bool(low <= ph <= high)

            if result["wq_class"] is None:
                unassessed += 1
            else:
                distribution[str(row.get("station_id", ""))][result["wq_class"]] += 1

        ctx.artifacts["class_distribution"] = {
            station: dict(counts) for station, counts in distribution.items()
        }
        ctx.meta["standard_name"] = self.standard.get("name")
        ctx.meta["standard_code"] = self.standard.get("standard_code")
        ctx.info(
            self.name,
            f"完成 {len(ctx.records)} 条记录评价，覆盖 {len(distribution)} 个站点，未评价 {unassessed} 条",
        )
        return ctx
