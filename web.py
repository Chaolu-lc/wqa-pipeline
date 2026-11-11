"""极简结果看板（仅使用 Python 标准库，无需安装任何 Web 框架）。

设计取舍：演示场景下，**启动可靠性优先于界面丰富度**。
用 ``http.server`` 直接托管一个只读页面，没有前端构建、没有额外依赖、没有端口冲突以外
的任何失败可能；页面每次请求都重新读取 ``report.json``，因此改配置重跑管线后刷新即见新结果。

启动：python web.py --port 8000
"""

from __future__ import annotations

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

REPORT_DIR = Path("reports")

STYLE = """
:root{--ink:#22303c;--muted:#6b7b87;--line:#e3e9ee;--bg:#f7f9fb;--card:#fff;--accent:#2f6db5}
*{box-sizing:border-box}
body{margin:0;font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;
     background:var(--bg);color:var(--ink);line-height:1.6}
.wrap{max-width:1120px;margin:0 auto;padding:28px 22px 60px}
header{border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:22px}
h1{margin:0 0 6px;font-size:24px;letter-spacing:.5px}
.sub{color:var(--muted);font-size:13px}
h2{font-size:16px;margin:30px 0 12px;padding-left:10px;border-left:4px solid var(--accent)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{color:var(--muted);font-size:12px}
.card .v{font-size:22px;font-weight:700;margin-top:2px}
.card .v small{font-size:12px;font-weight:400;color:var(--muted);margin-left:3px}
table{width:100%;border-collapse:collapse;background:var(--card);
      border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}
th{background:#eef3f8;font-weight:600;color:#3b4b57}
tr:last-child td{border-bottom:none}
.tag{display:inline-block;padding:1px 8px;border-radius:20px;font-size:12px;color:#fff}
.I{background:#2E7D6B}.II{background:#4CAF7D}.III{background:#F2C14E;color:#4a3a10}
.IV{background:#E8853A}.V{background:#D9534F}.劣V{background:#8E2B2B}
.charts{display:grid;gap:16px}
.charts figure{margin:0;background:var(--card);border:1px solid var(--line);
               border-radius:10px;padding:12px}
.charts img{width:100%;display:block;border-radius:6px}
figcaption{font-size:12px;color:var(--muted);margin-top:8px;text-align:center}
.alert{background:#fff4ef;border:1px solid #f3cdbd;border-radius:10px;padding:12px 16px;margin-bottom:10px;font-size:13px}
.alert b{color:#b0451f}
.empty{color:var(--muted);font-size:13px;padding:12px}
footer{margin-top:40px;color:var(--muted);font-size:12px;text-align:center}
"""


def _tag(value: str | None) -> str:
    if not value:
        return "-"
    return f'<span class="tag {html.escape(value)}">{html.escape(value)}</span>'


def render_page() -> str:
    path = REPORT_DIR / "report.json"
    if not path.exists():
        return (
            "<!doctype html><meta charset='utf-8'>"
            "<div style='font-family:sans-serif;padding:40px'>"
            "<h2>尚无分析结果</h2><p>请先运行：<code>python cli.py</code></p></div>"
        )

    report = json.loads(path.read_text(encoding="utf-8"))
    summary = report["summary"]
    quality = summary["data_quality"]
    validate = quality.get("validate") or {}
    missing = quality.get("missing") or {}
    outlier = quality.get("outlier") or {}
    pipeline = report.get("pipeline") or {}

    cards = [
        ("监测站点", summary["station_count"], "个"),
        ("有效记录", summary["final_rows"], "条"),
        ("原始记录", summary["raw_rows"], "条"),
        ("管线总耗时", f"{pipeline.get('total_elapsed_ms', 0):.1f}", "ms"),
    ]

    parts: list[str] = []
    parts.append("<!doctype html><meta charset='utf-8'><title>水质监测分析看板</title>")
    parts.append(f"<style>{STYLE}</style><div class='wrap'>")
    parts.append("<header><h1>水质监测数据智能分析看板</h1>")
    parts.append(
        f"<div class='sub'>评价依据：{html.escape(summary['standard']['name'])}"
        f"（{html.escape(summary['standard']['code'])}）　·　"
        f"时间范围：{html.escape(summary['time_range'][0])} ~ {html.escape(summary['time_range'][1])}"
        f"　·　生成于 {html.escape(summary['generated_at'])}</div></header>"
    )

    parts.append("<div class='cards'>")
    for key, value, unit in cards:
        parts.append(f"<div class='card'><div class='k'>{key}</div>"
                     f"<div class='v'>{value}<small>{unit}</small></div></div>")
    parts.append("</div>")

    # 数据质量
    parts.append("<h2>数据清洗效果</h2><table><tr><th>环节</th><th>结果</th></tr>")
    parts.append(
        f"<tr><td>校验过滤</td><td>类型错误 {validate.get('type_error', 0)}　量程越界 "
        f"{validate.get('range_error', 0)}　重复记录 {validate.get('duplicate', 0)}</td></tr>"
    )
    parts.append(
        f"<tr><td>缺失值填补</td><td>策略 <code>{missing.get('strategy')}</code>，"
        f"填补 {missing.get('filled', 0)} 个空值</td></tr>"
    )
    parts.append(
        f"<tr><td>异常值处理</td><td>截断孤立采集异常 {outlier.get('count', 0)} 条，"
        f"保留多指标共现事件 {outlier.get('event_samples', 0)} 条</td></tr>"
    )
    parts.append("</table>")

    # 站点评价
    parts.append("<h2>站点评价结果</h2>")
    parts.append("<table><tr><th>站点</th><th>采样次数</th><th>平均污染指数</th>"
                 "<th>最差类别</th><th>超标最突出的指标</th></tr>")
    labels = report.get("indicator_labels", {})
    for station in report["stations"]:
        rates = station["exceed_rate"]
        if rates and max(rates.values()) > 0:
            top = max(rates, key=rates.get)
            top_text = f"{labels.get(top, top)}（{rates[top] * 100:.1f}%）"
        else:
            top_text = "无超标"
        parts.append(
            f"<tr><td>{html.escape(station['station_name'])}</td>"
            f"<td>{station['samples']}</td>"
            f"<td>{station['avg_pollution_index']}</td>"
            f"<td>{_tag(station['worst_class'])}</td>"
            f"<td>{html.escape(top_text)}</td></tr>"
        )
    parts.append("</table>")

    # 预警
    parts.append("<h2>连续超标预警</h2>")
    if report["alerts"]:
        for alert in report["alerts"]:
            parts.append(
                f"<div class='alert'><b>{html.escape(alert['station_name'])}</b>："
                f"{html.escape(alert['start'])} 至 {html.escape(alert['end'])} 连续 "
                f"{alert['times']} 次达到 <b>{html.escape(alert['wq_class'])}</b> 类，"
                f"超标项 {html.escape(alert['exceed_items'])}</div>"
            )
    else:
        parts.append("<div class='empty'>本期未触发连续超标预警。</div>")

    # 图表
    charts = report.get("charts") or {}
    titles = {
        "class_distribution": "各站点综合水质类别分布",
        "timeline": "污染指数与氨氮时序演变",
        "exceed_heatmap": "站点 × 指标超标比例",
        "stage_waterfall": "各级过滤器耗时分布",
    }
    if charts:
        parts.append("<h2>结果可视化</h2><div class='charts'>")
        for key in ("class_distribution", "timeline", "exceed_heatmap", "stage_waterfall"):
            if key not in charts:
                continue
            name = Path(charts[key]).name
            parts.append(
                f"<figure><img src='/chart/{name}' alt='{titles[key]}'>"
                f"<figcaption>{titles[key]}</figcaption></figure>"
            )
        parts.append("</div>")

    # 运行轨迹
    if pipeline.get("stages"):
        parts.append("<h2>管线执行轨迹</h2><table><tr><th>序</th><th>过滤器</th>"
                     "<th>输入行</th><th>输出行</th><th>耗时(ms)</th><th>状态</th></tr>")
        for stage in pipeline["stages"]:
            parts.append(
                f"<tr><td>{stage['index']}</td><td>{html.escape(stage['label'])}</td>"
                f"<td>{stage['rows_in']}</td><td>{stage['rows_out']}</td>"
                f"<td>{stage['elapsed_ms']:.2f}</td><td>{stage['status']}</td></tr>"
            )
        parts.append("</table>")

    parts.append("<footer>管道-过滤器架构演示 · 数据由模拟脚本生成</footer></div>")
    return "".join(parts)


class Handler(BaseHTTPRequestHandler):
    server_version = "WQA-Dashboard/1.0"

    def do_GET(self) -> None:  # noqa: N802 - 标准库约定的方法名
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", render_page().encode("utf-8"))
        elif self.path.startswith("/chart/"):
            name = Path(unquote(self.path[len("/chart/"):])).name
            path = REPORT_DIR / name
            if path.is_file() and path.suffix.lower() == ".png":
                self._send(200, "image/png", path.read_bytes())
            else:
                self._send(404, "text/plain; charset=utf-8", "图片不存在".encode("utf-8"))
        else:
            self._send(404, "text/plain; charset=utf-8", "404".encode("utf-8"))

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # 静默访问日志，保持控制台干净
        return


def main() -> int:
    global REPORT_DIR

    parser = argparse.ArgumentParser(description="启动结果看板")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()

    REPORT_DIR = Path(args.report_dir)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"看板已启动：http://{args.host}:{args.port}")
    print(f"数据来源：{REPORT_DIR / 'report.json'}")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
