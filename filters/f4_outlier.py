"""阶段 4 · 异常值处理过滤器。

**核心设计判断**：不是所有"数值偏离"都该被当成噪声。水质数据里有两类异常：

    1. **采集异常**——探头脏污、通讯丢包、单位错乱。特征：孤立出现，只有单个指标突变；
    2. **真实污染事件**——偷排、事故性排放。特征：多个相关指标**同时**偏离。

把第 2 类当噪声处理（截断/删除）会直接抹掉系统最该报警的信号。因此本过滤器采用
"多指标共现判定"（corroboration）：同一时刻有 ≥N 个指标同时越界则判为疑似真实事件，
**保留原值**；只有孤立单指标越界才按配置动作处置。

这既是技术判断，也是本文档中"异常处理不能只看统计分布"的设计论点。
"""

from __future__ import annotations

from collections import defaultdict

from pipeline.base import Filter, FilterContext, FilterError
from pipeline.engine import register

MAX_DETAILS = 20  # 报告中最多列出的明细条数


@register
class OutlierFilter(Filter):
    """按站点分组识别异常值，并区分采集异常与真实污染事件。"""

    name = "outlier"
    label = "异常值处理"

    def process(self, ctx: FilterContext) -> FilterContext:
        if not ctx.records:
            raise FilterError("上游未提供任何数据")

        method: str = self.config.get("method", "iqr")
        k: float = float(self.config.get("k", 1.5))
        action: str = self.config.get("action", "clip")
        min_samples: int = int(self.config.get("min_samples", 8))
        corroboration: int = int(self.config.get("corroboration", 2))
        fields: list[str] = self.config.get("fields", [])

        if action not in {"clip", "tag", "drop"}:
            raise FilterError(f"不支持的异常处置动作：{action}")

        groups: dict[str, list[dict]] = defaultdict(list)
        for row in ctx.records:
            groups[str(row.get("station_id", ""))].append(row)

        handled = 0          # 被判为采集异常并已处置的条数
        corroborated = 0     # 多指标共现、判为真实事件的条数
        details: list[dict] = []
        drop_ids: set[int] = set()

        for station, rows in groups.items():
            bounds = self._field_bounds(rows, fields, method, k, min_samples)

            for row in rows:
                flagged = [
                    field
                    for field, (low, high) in bounds.items()
                    if row.get(field) is not None and not (low <= row[field] <= high)
                ]
                if not flagged:
                    continue

                # 多指标同时越界 → 判为疑似真实污染事件，保留原值只做标记
                if len(flagged) >= corroboration:
                    corroborated += 1
                    row["_event_suspected"] = True
                    row["_event_fields"] = ",".join(flagged)
                    continue

                handled += 1
                for field in flagged:
                    low, high = bounds[field]
                    value = row[field]
                    if action == "clip":
                        row[field] = round(low if value < low else high, 3)
                    elif action == "tag":
                        row[f"_outlier_{field}"] = True
                    else:
                        drop_ids.add(id(row))
                    if len(details) < MAX_DETAILS:
                        details.append(
                            {
                                "station": station,
                                "field": field,
                                "time": str(row.get("sample_time")),
                                "value": round(value, 3),
                                "bounds": [round(low, 3), round(high, 3)],
                            }
                        )

        if action == "drop" and drop_ids:
            ctx.records = [row for row in ctx.records if id(row) not in drop_ids]

        stats = {
            "method": method,
            "action": action,
            "corroboration": corroboration,
            "count": handled,
            "event_samples": corroborated,
            "details": details,
        }
        ctx.meta["outlier"] = stats
        ctx.artifacts["outliers"] = details
        ctx.info(
            self.name,
            f"{method} 法：处置采集异常 {handled} 条，保留多指标共现事件 {corroborated} 条",
        )
        if corroborated:
            ctx.warn(self.name, f"检测到 {corroborated} 条疑似真实污染事件样本（多指标同时越界）")
        return ctx

    def _field_bounds(
        self,
        rows: list[dict],
        fields: list[str],
        method: str,
        k: float,
        min_samples: int,
    ) -> dict[str, tuple[float, float]]:
        """为每个指标计算合理区间；样本不足时不参与判定。"""
        bounds: dict[str, tuple[float, float]] = {}
        for field in fields:
            values = [row.get(field) for row in rows if row.get(field) is not None]
            if len(values) < min_samples:
                continue
            low, high = self._bounds(values, method, k)
            if low is None or high is None:
                continue
            bounds[field] = (low, high)
        return bounds

    @staticmethod
    def _bounds(values: list[float], method: str, k: float):
        if method == "sigma":
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std = variance**0.5
            return mean - k * std, mean + k * std
        if method == "iqr":
            ordered = sorted(values)
            q1 = _percentile(ordered, 0.25)
            q3 = _percentile(ordered, 0.75)
            iqr = q3 - q1
            if iqr == 0:
                return None, None
            return q1 - k * iqr, q3 + k * iqr
        raise FilterError(f"不支持的异常检测方法：{method}")


def _percentile(ordered: list[float], p: float) -> float:
    """线性插值法计算分位数（不依赖第三方库）。"""
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac
