"""阶段 2 · 数据校验过滤器。

把"脏数据"分三类处理，每类给出不同的处置策略（这是刻意的设计决策）：

    - 类型错误 / 物理量程越界 → 置为空值，交给下一级统一填补（不在这里擅自猜测真值）
    - 时间无法解析 → 该行直接丢弃（没有时间戳的记录在下游无法排序与插值，无使用价值）
    - 站点+时间重复 → 保留首次出现（同一时刻同一站点的重复上报视为采集端重复写入）

这样设计的好处：校验阶段只做"定性判断"，不做"数值修补"，
职责边界清晰，也让下游缺失值过滤器成为唯一的数据修补入口。
"""

from __future__ import annotations

from datetime import datetime

from pipeline.base import Filter, FilterContext, FilterError
from pipeline.engine import register

DEFAULT_TIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
]


@register
class ValidateFilter(Filter):
    """字段完整性、数值类型、时间格式、重复记录、物理量程五重校验。"""

    name = "validate"
    label = "数据校验"

    def process(self, ctx: FilterContext) -> FilterContext:
        if not ctx.records:
            raise FilterError("上游未提供任何数据")

        required: list[str] = self.config.get("required_fields", [])
        numeric_fields: list[str] = self.config.get("numeric_fields", [])
        time_formats: list[str] = self.config.get("time_formats", DEFAULT_TIME_FORMATS)
        ranges: dict = self.config.get("physical_ranges", {})

        missing_cols = [col for col in required if col not in ctx.records[0]]
        if missing_cols:
            raise FilterError(f"缺少必需字段：{', '.join(missing_cols)}")

        stats = {"type_error": 0, "range_error": 0, "time_error": 0, "duplicate": 0, "kept": 0}
        cleaned: list[dict] = []
        seen: set[tuple[str, datetime]] = set()

        for row in ctx.records:
            for field in numeric_fields:
                raw = row.get(field)
                if raw is None or str(raw).strip() == "":
                    row[field] = None
                    continue
                try:
                    value = float(str(raw).strip())
                except (TypeError, ValueError):
                    stats["type_error"] += 1
                    row[field] = None
                    continue

                bounds = ranges.get(field)
                if bounds:
                    low, high = bounds
                    if value < low or value > high:
                        stats["range_error"] += 1
                        row[field] = None
                        continue
                row[field] = value

            parsed = self._parse_time(row.get("sample_time"), time_formats)
            if parsed is None:
                stats["time_error"] += 1
                continue
            row["sample_time"] = parsed

            key = (str(row.get("station_id", "")).strip(), parsed)
            if key in seen:
                stats["duplicate"] += 1
                continue
            seen.add(key)
            cleaned.append(row)

        stats["kept"] = len(cleaned)
        ctx.records = cleaned
        ctx.meta["validate"] = stats
        ctx.info(
            self.name,
            f"保留 {len(cleaned)} 行；类型错误 {stats['type_error']}、量程越界 "
            f"{stats['range_error']}、时间异常 {stats['time_error']}、重复 {stats['duplicate']}",
        )
        return ctx

    @staticmethod
    def _parse_time(value, formats: list[str]) -> datetime | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None
