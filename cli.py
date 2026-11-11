"""命令行入口：加载配置 → 组装管线 → 逐级执行 → 输出报告。

这个文件属于**编排层**，不含任何业务逻辑。它只做三件事：
    1. 读配置、应用命令行参数覆盖；
    2. 订阅引擎的执行事件，把每级进度实时打印出来（现场演示的主力）；
    3. 管线结束后补齐运行轨迹并汇总输出路径。

命令行参数全部是"覆盖配置"，而不是"另写一套逻辑"——
保证 CLI 与 Web 两种入口跑的是同一条管线、同一份配置。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from filters.f7_report import finalize_report
from pipeline.base import FilterContext
from pipeline.engine import Pipeline, StageTrace, available_filters


def _print_stage(trace: StageTrace) -> None:
    """逐级打印：序号、过滤器名、行数进出、耗时、状态。

    现场演示时，这张表就是"数据在管道中逐级流动"的直观证据，
    也是各阶段性能数据的第一手来源。
    """
    status = "OK  " if trace.status == "OK" else trace.status
    note = f"  ({trace.note})" if trace.note else ""
    print(
        f"  [{trace.index}/{trace.total}] {trace.label:<6} "
        f"{trace.rows_in:>5} 行 → {trace.rows_out:<5} 行 "
        f"{trace.elapsed_ms:>8.2f} ms   {status}{note}"
    )
    if trace.error:
        print(f"           └─ 错误：{trace.error}")


def _load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"配置文件不存在：{path}")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or "stages" not in config:
        raise SystemExit("配置文件格式错误：缺少 stages 节点")
    return config


def build_stages(config: dict, args: argparse.Namespace) -> list[tuple[str, dict]]:
    """把配置转成引擎需要的 (名称, 配置) 列表，并应用命令行覆盖。"""
    stages: list[tuple[str, dict]] = []
    for item in config["stages"]:
        name = item["name"]
        params = dict(item.get("config") or {})
        if args.data and name == "loader":
            params["patterns"] = [str(Path(args.data) / "*.csv")]
        if args.output and name == "report":
            params["output_dir"] = args.output
        stages.append((name, params))
    return stages


def _warmup() -> None:
    """预热绘图库。

    matplotlib 首次导入需要 1~2 秒，这部分开销与"数据处理"无关，
    若计入报表过滤器耗时会掩盖各级真实耗时差异。演示前预加载，
    使实测数据反映的是渲染本身而非库加载。
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
    except Exception:  # noqa: BLE001 - 缺绘图库时由报表过滤器自行降级
        pass


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认编码可能无法输出中文，统一为 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 非关键路径
        pass

    parser = argparse.ArgumentParser(
        description="水质监测数据智能分析管线（管道-过滤器架构演示）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n  python cli.py\n  python cli.py --data data/raw --output reports\n"
               "  python cli.py --config config/pipeline_simple.yaml",
    )
    parser.add_argument("--config", default="config/pipeline.yaml", help="管线配置文件")
    parser.add_argument("--data", help="覆盖数据目录")
    parser.add_argument("--output", help="覆盖报告输出目录")
    parser.add_argument("--on-error", choices=["skip", "abort"], help="覆盖异常隔离策略")
    parser.add_argument("--list-filters", action="store_true", help="列出已注册的过滤器")
    parser.add_argument("--quiet", action="store_true", help="只输出结果汇总")
    parser.add_argument("--sample", type=int, default=3, help="末尾展示的记录条数，0 表示不展示")
    args = parser.parse_args(argv)

    if args.list_filters:
        print("已注册过滤器（按配置中的 name 引用）：")
        for name in available_filters():
            print(f"  - {name}")
        return 0

    config = _load_config(Path(args.config))
    stages = build_stages(config, args)
    on_error = args.on_error or (config.get("pipeline") or {}).get("on_error", "skip")

    print("=" * 68)
    print("水质监测数据智能分析管线 · 管道-过滤器架构")
    print("=" * 68)
    print(f"配置文件：{args.config}")
    print(f"过滤链　：{' → '.join(name for name, _ in stages)}")
    print(f"隔离策略：{on_error}")
    print("-" * 68)

    context = FilterContext(meta={"config_path": args.config})
    _warmup()
    pipeline = Pipeline(stages, on_error=on_error)
    result = pipeline.run(context, observer=None if args.quiet else _print_stage)

    report_dir = next(
        (cfg.get("output_dir", "reports") for name, cfg in stages if name == "report"),
        "reports",
    )
    finalize_report(report_dir, result)

    print("-" * 68)
    station_count = len(context.artifacts.get("trends", {})) or len(
        {row.get("station_id") for row in context.records}
    )
    print(
        f"管线状态：{result.status}　"
        f"总耗时：{result.total_elapsed_ms:.2f} ms　"
        f"输出记录：{context.row_count} 条　"
        f"站点：{station_count} 个"
    )

    if args.sample:
        print("\n最终记录样例（已带评价结果）：")
        for row in context.records[: args.sample]:
            print(
                f"  {row.get('station_name')} {row.get('sample_time')}　"
                f"氨氮 {row.get('nh3n')}　类别 {row.get('wq_class')}　"
                f"污染指数 {row.get('pollution_index')}"
            )

    print(f"\n报告：{report_dir}/report.md、{report_dir}/report.json")
    charts = context.artifacts.get("report", {})
    if charts:
        print(f"图表目录：{report_dir}/chart_*.png")
    print(f"启动看板：python web.py --port 8000")

    if result.status != "OK":
        print("\n[注意] 存在被隔离的失败阶段：管线未中断，已完成阶段的结果仍然有效。")
    return 0 if result.status == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
