"""阶段 6 · 趋势分析与预警过滤器。

在评价结果之上做三件事：
    1. 各站点各指标的**日均变化斜率**（最小二乘），回答"在变好还是变差"；
    2. **连续超标预警**：连续 N 次采样达到预警类别即生成告警，
       比"单次超标"更有工程意义——偶发单点更可能是异常值，连续超标才是真实污染过程；
    3. **站点排名**：按平均污染指数排序，供管理侧聚焦。
"""

from __future__ import annotations

from collections import defaultdict

from pipeline.base import Filter, FilterContext, FilterError
from pipeline.engine import register
from pipeline.standards import class_rank


@register
class TrendFilter(Filter):
    """时序趋势、连续超标预警与站点排名。"""

    name = "trend"
    label = "趋势与预警"

    def process(self, ctx: FilterContext) -> FilterContext:
        if not ctx.records:
            raise FilterError("上游未提供任何数据")

        consecutive: int = int(self.config.get("consecutive", 3))
        warn_class: str = self.config.get("warn_class", "IV")
        threshold = class_rank(warn_class)
        fields: list[str] = self.config.get("fields") or [
            key[6:] for key in ctx.records[0] if key.startswith("class_")
        ]

        groups: dict[str, list[dict]] = defaultdict(list)
        for row in ctx.records:
            groups[str(row.get("station_id", ""))].append(row)

        trends: dict[str, dict] = {}
        alerts: list[dict] = []
        exceed_rate: dict[str, dict[str, float]] = {}

        for station, rows in groups.items():
            rows.sort(key=lambda r: r["sample_time"])

            # ---- 连续超标预警：一段连续超标只输出一条告警 ----
            # 早期实现是"每满 N 次就报一条"，会把一次持续数天的污染事件拆成多条碎片，
            # 反而不利于定位。这里改为按连续段聚合，告警覆盖整个事件区间。
            active: dict | None = None
            for row in rows:
                rank_now = class_rank(row.get("wq_class"))
                if rank_now >= threshold:
                    if active is None:
                        active = {
                            "station": station,
                            "station_name": row.get("station_name", station),
                            "start": str(row["sample_time"]),
                            "end": str(row["sample_time"]),
                            "times": 0,
                            "wq_class": row.get("wq_class"),
                            "exceed_items": row.get("exceed_items", ""),
                            "_worst": rank_now,
                        }
                    active["times"] += 1
                    active["end"] = str(row["sample_time"])
                    if rank_now > active["_worst"]:
                        active["_worst"] = rank_now
                        active["wq_class"] = row.get("wq_class")
                        active["exceed_items"] = row.get("exceed_items", "")
                else:
                    if active and active["times"] >= consecutive:
                        active.pop("_worst")
                        alerts.append(active)
                    active = None

            if active and active["times"] >= consecutive:
                active.pop("_worst")
                alerts.append(active)

            # ---- 指标趋势斜率（单位：每天） ----
            t0 = rows[0]["sample_time"]
            slopes: dict[str, float] = {}
            for field in fields:
                points = []
                for row in rows:
                    value = row.get(field)
                    if value is None:
                        continue
                    days = (row["sample_time"] - t0).total_seconds() / 86400
                    points.append((days, float(value)))
                slopes[field] = round(_least_squares_slope(points), 4)

            # ---- 超标率（用于热力图） ----
            rates: dict[str, float] = {}
            for field in fields:
                values = [r.get(f"class_{field}") for r in rows]
                values = [c for c in values if c]
                if values:
                    bad = sum(1 for c in values if class_rank(c) >= 4)
                    rates[field] = round(bad / len(values), 4)
            exceed_rate[station] = rates

            indices = [r["pollution_index"] for r in rows if r.get("pollution_index") is not None]
            trends[station] = {
                "station_name": rows[0].get("station_name", station),
                "samples": len(rows),
                "start": str(rows[0]["sample_time"]),
                "end": str(rows[-1]["sample_time"]),
                "avg_pollution_index": round(sum(indices) / len(indices), 3) if indices else None,
                "max_pollution_index": round(max(indices), 3) if indices else None,
                "slopes_per_day": slopes,
            }

        ranking = sorted(
            (
                {
                    "station": station,
                    "station_name": info["station_name"],
                    "avg_pollution_index": info["avg_pollution_index"],
                }
                for station, info in trends.items()
            ),
            key=lambda item: item["avg_pollution_index"] or 0,
            reverse=True,
        )
        for position, item in enumerate(ranking, start=1):
            item["rank"] = position

        ctx.artifacts["trends"] = trends
        ctx.artifacts["alerts"] = alerts
        ctx.artifacts["ranking"] = ranking
        ctx.artifacts["exceed_rate"] = exceed_rate

        ctx.info(
            self.name,
            f"计算 {len(trends)} 个站点趋势，触发 {len(alerts)} 条连续超标预警",
        )
        return ctx


def _least_squares_slope(points: list[tuple[float, float]]) -> float:
    """一元线性回归斜率；样本不足时返回 0。"""
    n = len(points)
    if n < 2:
        return 0.0
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    return numerator / denominator if denominator else 0.0
