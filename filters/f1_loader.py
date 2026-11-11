"""阶段 1 · 数据加载过滤器。

职责单一：把磁盘上的原始 CSV 变成内存中的记录列表。
它**不解释数据含义**，也不做任何清洗——那是下游过滤器的责任。
"""

from __future__ import annotations

import csv
import glob
from pathlib import Path

from pipeline.base import Filter, FilterContext, FilterError
from pipeline.engine import register


@register
class LoaderFilter(Filter):
    """从多个 CSV 文件读取原始监测数据并合并。"""

    name = "loader"
    label = "数据加载"

    def process(self, ctx: FilterContext) -> FilterContext:
        patterns: list[str] = self.config.get("patterns") or ["data/raw/*.csv"]
        encoding: str = self.config.get("encoding", "utf-8-sig")

        paths: list[Path] = []
        for pattern in patterns:
            for hit in sorted(glob.glob(pattern, recursive=True)):
                candidate = Path(hit)
                if candidate.is_file() and candidate.suffix.lower() == ".csv":
                    paths.append(candidate)

        if not paths:
            raise FilterError(f"未匹配到任何 CSV 文件：{patterns}")

        records: list[dict] = []
        loaded: list[dict] = []
        for path in paths:
            try:
                with path.open("r", encoding=encoding, newline="") as fh:
                    rows = [dict(row) for row in csv.DictReader(fh)]
            except UnicodeDecodeError:
                ctx.warn(self.name, f"编码不符，已跳过文件：{path.name}")
                continue
            except OSError as exc:
                ctx.warn(self.name, f"读取失败，已跳过文件：{path.name}（{exc}）")
                continue

            for row in rows:
                row["_source_file"] = path.name
            records.extend(rows)
            loaded.append({"file": path.name, "rows": len(rows)})

        if not records:
            raise FilterError("所有数据文件均读取失败或内容为空")

        ctx.records = records
        ctx.meta["source_files"] = loaded
        ctx.meta["raw_row_count"] = len(records)
        ctx.info(self.name, f"读取 {len(loaded)} 个文件，共 {len(records)} 行")
        return ctx
