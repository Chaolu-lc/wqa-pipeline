"""阶段 3 · 缺失值填补过滤器。

策略通过配置切换（``linear`` / ``mean`` / ``median`` / ``drop``），
这是策略模式在过滤器内部的应用：过滤器的**位置**在管线中固定，
但**算法**可以随配置替换，两者解耦。

插入方式按站点分组，避免把 A 站的浓度规律套到 B 站。
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from pipeline.base import Filter, FilterContext, FilterError
from pipeline.engine import register

STRATEGIES = {"linear", "mean", "median", "drop"}


@register
class MissingValueFilter(Filter):
    """按站点分组填补缺失值。"""

    name = "missing"
    label = "缺失值填补"

    def process(self, ctx: FilterContext) -> FilterContext:
        if not ctx.records:
            raise FilterError("上游未提供任何数据")

        strategy: str = self.config.get("strategy", "linear")
        if strategy not in STRATEGIES:
            raise FilterError(f"不支持的填补策略：{strategy}")
        max_ratio: float = float(self.config.get("max_fill_ratio", 1.0))
        fields: list[str] = self.config.get("fields") or self._numeric_fields(ctx.records)

        groups: dict[str, list[dict]] = defaultdict(list)
        for row in ctx.records:
            groups[str(row.get("station_id", ""))].append(row)

        filled = 0
        abandoned: list[str] = []

        for station, rows in groups.items():
            rows.sort(key=lambda r: r["sample_time"])
            for field in fields:
                present = [r.get(field) for r in rows if r.get(field) is not None]
                missing_rows = [r for r in rows if r.get(field) is None]
                if not missing_rows:
                    continue
                # 全空或缺失比例过高：强插会伪造数据，宁可标记为不可用
                if not present or len(missing_rows) / len(rows) > max_ratio:
                    abandoned.append(f"{station}.{field}")
                    continue

                if strategy == "mean":
                    value = sum(present) / len(present)
                    for row in missing_rows:
                        row[field] = value
                elif strategy == "median":
                    value = statistics.median(present)
                    for row in missing_rows:
                        row[field] = value
                elif strategy == "linear":
                    self._linear_fill(rows, field)
                filled += len(missing_rows)

        dropped = 0
        if strategy == "drop":
            before = len(ctx.records)
            ctx.records = [
                row for row in ctx.records
                if all(row.get(f) is not None for f in fields)
            ]
            dropped = before - len(ctx.records)

        stats = {
            "strategy": strategy,
            "filled": filled,
            "dropped": dropped,
            "abandoned": abandoned,
            "remaining_missing": sum(
                1 for row in ctx.records for f in fields if row.get(f) is None
            ),
        }
        ctx.meta["missing"] = stats
        ctx.info(
            self.name,
            f"策略={strategy}，填补 {filled} 个空值，放弃清洗 {len(abandoned)} 个站-指标组合",
        )
        return ctx

    @staticmethod
    def _linear_fill(rows: list[dict], field: str) -> None:
        """按时间顺序做线性插值；两端用最近的有效值延展。"""
        values = [row.get(field) for row in rows]
        known = [i for i, v in enumerate(values) if v is not None]
        if not known:
            return

        first, last = known[0], known[-1]
        for i in range(first):
            values[i] = values[first]
        for i in range(last + 1, len(values)):
            values[i] = values[last]

        for left, right in zip(known, known[1:]):
            if right - left <= 1:
                continue
            span = right - left
            for i in range(left + 1, right):
                ratio = (i - left) / span
                values[i] = values[left] + (values[right] - values[left]) * ratio

        for row, value in zip(rows, values):
            row[field] = value

    @staticmethod
    def _numeric_fields(records: list[dict]) -> list[str]:
        fields = {
            key for row in records for key, value in row.items() if isinstance(value, float)
        }
        return sorted(fields)
