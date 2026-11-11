"""阶段 7 · 报表生成过滤器。

管线末端，把前面各级积累的评价结果、趋势、预警汇成两份可直接交付的材料：

    - ``report.json``：结构化数据，供 Web 看板读取；
    - ``report.md``  ：人读报告，图片直接内嵌引用，可直接贴进设计文档。

注意：**运行轨迹（各级耗时）不在本过滤器内产出**。因为最后一级执行时管线尚未结束，
耗时数据还不完整，所以由编排层在管线跑完后调用 ``finalize_report()`` 补齐。
这是"数据依赖决定执行顺序"的一个真实例子。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pipeline import charts
from pipeline.base import Filter, FilterContext, FilterError
from pipeline.engine import register
from pipeline.standards import class_rank, load_standard


@register
class ReportFilter(Filter):
    """聚合结果、渲染图表、输出 JSON 与 Markdown 报告。"""

    name = "report"
    label = "报表生成"

    def setup(self) -> None:
        path = self.config.get("standard", "config/water_quality_standard.yaml")
        self.standard = load_standard(path)

    def process(self, ctx: FilterContext) -> FilterContext:
        if not ctx.records:
            raise FilterError("上游未提供任何数据")

        out_dir = Path(self.config.get("output_dir", "reports"))
        out_dir.mkdir(parents=True, exist_ok=True)
        formats: list[str] = self.config.get("formats", ["json", "md"])
        want_charts = bool(self.config.get("charts", True))

        report = self._build_report(ctx, out_dir, want_charts)

        if "json" in formats:
            (out_dir / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        if "md" in formats:
            (out_dir / "report.md").write_text(
                self._render_markdown(report), encoding="utf-8"
            )

        ctx.artifacts["report"] = {"dir": str(out_dir), "formats": formats}
        ctx.info(self.name, f"报告已生成：{out_dir}（{'/'.join(formats)}）")
        return ctx

    # ------------------------------------------------------------------
    # 报告组装
    # ------------------------------------------------------------------
    def _build_report(self, ctx: FilterContext, out_dir: Path, want_charts: bool) -> dict:
        station_names = {
            str(row.get("station_id", "")): row.get("station_name", "")
            for row in ctx.records
        }
        trends: dict = ctx.artifacts.get("trends", {})
        distribution: dict = ctx.artifacts.get("class_distribution", {})
        exceed_rate: dict = ctx.artifacts.get("exceed_rate", {})
        alerts: list = ctx.artifacts.get("alerts", [])
        ranking: list = ctx.artifacts.get("ranking", [])

        indicators = list(self.standard["indicators"].keys())
        field_labels = {key: cfg["name"] for key, cfg in self.standard["indicators"].items()}

        chart_map: dict[str, str] = {}
        if want_charts:
            try:
                chart_map["class_distribution"] = charts.class_distribution_chart(
                    distribution, station_names, out_dir / "chart_class.png"
                )
                focus = "nh3n" if "nh3n" in indicators else indicators[0]
                chart_map["timeline"] = charts.pollution_timeline_chart(
                    ctx.records,
                    station_names,
                    focus,
                    field_labels.get(focus, focus),
                    out_dir / "chart_timeline.png",
                )
                chart_map["exceed_heatmap"] = charts.exceed_rate_heatmap(
                    exceed_rate, station_names, indicators, field_labels, out_dir / "chart_heatmap.png"
                )
            except Exception as exc:  # noqa: BLE001 - 图表失败不应影响报告主体
                ctx.warn(self.name, f"图表渲染失败，报告仅输出表格：{exc}")

        stations = []
        for station, info in trends.items():
            counts = distribution.get(station, {})
            worst = max(counts, key=class_rank) if counts else None
            stations.append(
                {
                    "station_id": station,
                    "station_name": info["station_name"],
                    "samples": info["samples"],
                    "avg_pollution_index": info["avg_pollution_index"],
                    "max_pollution_index": info["max_pollution_index"],
                    "worst_class": worst,
                    "class_counts": counts,
                    "exceed_rate": exceed_rate.get(station, {}),
                    "slopes_per_day": info["slopes_per_day"],
                }
            )
        stations.sort(key=lambda item: class_rank(item["worst_class"]), reverse=True)

        times = [row["sample_time"] for row in ctx.records]
        outlier_meta = dict(ctx.meta.get("outlier") or {})
        outlier_meta.pop("details", None)

        return {
            "summary": {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "standard": {
                    "name": self.standard.get("name"),
                    "code": self.standard.get("standard_code"),
                    "scenario": self.standard.get("scenario"),
                },
                "station_count": len(trends),
                "raw_rows": ctx.meta.get("raw_row_count"),
                "final_rows": len(ctx.records),
                "time_range": [
                    min(times).isoformat(sep=" "),
                    max(times).isoformat(sep=" "),
                ],
                "source_files": ctx.meta.get("source_files", []),
                "data_quality": {
                    "validate": ctx.meta.get("validate"),
                    "missing": ctx.meta.get("missing"),
                    "outlier": outlier_meta,
                },
            },
            "stations": stations,
            "ranking": ranking,
            "alerts": alerts,
            "outliers": ctx.artifacts.get("outliers", []),
            "charts": chart_map,
            "indicator_labels": field_labels,
        }

    # ------------------------------------------------------------------
    # Markdown 渲染
    # ------------------------------------------------------------------
    def _render_markdown(self, report: dict) -> str:
        summary = report["summary"]
        quality = summary["data_quality"]
        lines: list[str] = []

        lines.append("# 水质监测数据智能分析报告")
        lines.append("")
        lines.append(
            f"> 生成时间：{summary['generated_at']}　|　评价依据："
            f"{summary['standard']['name']}（{summary['standard']['code']}，{summary['standard']['scenario']}）"
        )
        lines.append("")

        # 一、数据概况
        lines.append("## 一、数据概况")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("| --- | --- |")
        lines.append(f"| 监测站点数 | {summary['station_count']} |")
        lines.append(f"| 原始记录数 | {summary['raw_rows']} |")
        lines.append(f"| 有效记录数 | {summary['final_rows']} |")
        lines.append(f"| 时间范围 | {summary['time_range'][0]} ~ {summary['time_range'][1]} |")
        lines.append("")

        if summary["source_files"]:
            lines.append("数据来源文件：")
            lines.append("")
            for item in summary["source_files"]:
                lines.append(f"- `{item['file']}`：{item['rows']} 行")
            lines.append("")

        # 二、数据质量
        lines.append("## 二、数据质量与清洗结果")
        lines.append("")
        validate = quality.get("validate") or {}
        missing = quality.get("missing") or {}
        outlier = quality.get("outlier") or {}
        lines.append("| 处理环节 | 结果 |")
        lines.append("| --- | --- |")
        lines.append(
            f"| 校验过滤 | 类型错误 {validate.get('type_error', 0)}、量程越界 "
            f"{validate.get('range_error', 0)}、时间异常 {validate.get('time_error', 0)}、"
            f"重复记录 {validate.get('duplicate', 0)} |"
        )
        lines.append(
            f"| 缺失值填补 | 策略 `{missing.get('strategy')}`，填补 {missing.get('filled', 0)} 个空值，"
            f"放弃 {len(missing.get('abandoned', []))} 个站-指标组合 |"
        )
        lines.append(
            f"| 异常值处理 | 方法 `{outlier.get('method')}`，处置方式 `{outlier.get('action')}`："
            f"截断孤立采集异常 {outlier.get('count', 0)} 条，保留多指标共现事件 "
            f"{outlier.get('event_samples', 0)} 条 |"
        )
        lines.append("")

        # 三、站点评价
        lines.append("## 三、站点评价结果")
        lines.append("")
        lines.append("| 站点 | 采样次数 | 平均污染指数 | 最差类别 | 超标最突出的指标 |")
        lines.append("| --- | --- | --- | --- | --- |")
        labels = report.get("indicator_labels", {})
        for station in report["stations"]:
            rates = station["exceed_rate"]
            if rates and max(rates.values()) > 0:
                top = max(rates, key=rates.get)
                top_text = f"{labels.get(top, top)}（{rates[top] * 100:.1f}%）"
            else:
                top_text = "无超标"
            lines.append(
                f"| {station['station_name']} | {station['samples']} | "
                f"{station['avg_pollution_index']} | {station['worst_class']} | {top_text} |"
            )
        lines.append("")

        # 四、图表
        chart_titles = {
            "class_distribution": "图 1　各站点综合水质类别分布",
            "timeline": "图 2　污染指数与氨氮时序演变",
            "exceed_heatmap": "图 3　站点 × 指标超标比例",
        }
        if report.get("charts"):
            lines.append("## 四、结果可视化")
            lines.append("")
            for key, path in report["charts"].items():
                title = chart_titles.get(key)
                if not title:
                    continue
                lines.append(f"**{title}**")
                lines.append("")
                lines.append(f"![{title}]({Path(path).name})")
                lines.append("")

        # 五、预警
        lines.append("## 五、连续超标预警")
        lines.append("")
        if report["alerts"]:
            lines.append("| 站点 | 起始时间 | 结束时间 | 持续次数 | 类别 | 超标项 |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for alert in report["alerts"]:
                lines.append(
                    f"| {alert['station_name']} | {alert['start']} | {alert['end']} | "
                    f"{alert['times']} | {alert['wq_class']} | {alert['exceed_items']} |"
                )
        else:
            lines.append("本期未触发连续超标预警。")
        lines.append("")

        # 六、异常值样例
        if report["outliers"]:
            lines.append("## 六、异常值识别样例（前 10 条）")
            lines.append("")
            lines.append("| 站点 | 指标 | 时间 | 原始值 | 合理区间 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for item in report["outliers"][:10]:
                lines.append(
                    f"| {item['station']} | {item['field']} | {item['time']} | "
                    f"{item['value']} | [{item['bounds'][0]}, {item['bounds'][1]}] |"
                )
            lines.append("")

        return "\n".join(lines)


# ----------------------------------------------------------------------
# 编排层调用：管线结束后补齐运行轨迹
# ----------------------------------------------------------------------
def finalize_report(report_dir: str | Path, result) -> dict:
    """把管线执行轨迹写回报告，并渲染各级耗时图。

    Args:
        report_dir: 报告输出目录。
        result: ``PipelineResult`` 实例。
    """
    report_dir = Path(report_dir)
    json_path = report_dir / "report.json"
    if not json_path.exists():
        return {}

    report = json.loads(json_path.read_text(encoding="utf-8"))
    trace = result.as_dict()
    report["pipeline"] = trace

    try:
        chart = charts.stage_waterfall_chart(result.stages, report_dir / "chart_stages.png")
        report.setdefault("charts", {})["stage_waterfall"] = chart
    except Exception:  # noqa: BLE001 - 图表非关键路径
        pass

    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    md_path = report_dir / "report.md"
    if md_path.exists():
        md_path.write_text(
            md_path.read_text(encoding="utf-8") + _render_pipeline_section(trace),
            encoding="utf-8",
        )
    return report


def _render_pipeline_section(trace: dict) -> str:
    lines = ["", "## 七、管线执行轨迹", ""]
    lines.append(f"整体状态：`{trace['status']}`　总耗时：`{trace['total_elapsed_ms']} ms`")
    lines.append("")
    lines.append("| 序号 | 过滤器 | 输入行数 | 输出行数 | 耗时(ms) | 状态 | 备注 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for stage in trace["stages"]:
        lines.append(
            f"| {stage['index']} | {stage['label']} | {stage['rows_in']} | {stage['rows_out']} | "
            f"{stage['elapsed_ms']:.3f} | {stage['status']} | {stage['note'] or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)
