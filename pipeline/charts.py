"""图表渲染（与业务逻辑解耦的纯展示层）。

所有图都以 PNG 落盘，供 Markdown 报告引用与课堂 PPT 直接取用。
统一在这里设置中文字体与配色，避免各过滤器各自为政。
"""

from __future__ import annotations

from pathlib import Path

CLASS_COLORS = {
    "I": "#2E7D6B",
    "II": "#4CAF7D",
    "III": "#F2C14E",
    "IV": "#E8853A",
    "V": "#D9534F",
    "劣V": "#8E2B2B",
}
CLASS_ORDER = ["I", "II", "III", "IV", "V", "劣V"]
STATION_COLORS = ["#2F6DB5", "#E8853A", "#4CAF7D", "#8E6BB5", "#D9534F"]
INK = "#22303C"
GRID = "#DCE3E8"


def _plt():
    """延迟导入并统一配置 matplotlib，避免无显示环境下报错。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["axes.edgecolor"] = "#9AA8B2"
    plt.rcParams["text.color"] = INK
    plt.rcParams["axes.labelcolor"] = INK
    plt.rcParams["xtick.color"] = INK
    plt.rcParams["ytick.color"] = INK
    return plt


def class_distribution_chart(
    distribution: dict[str, dict[str, int]],
    station_names: dict[str, str],
    out_path: Path,
) -> str:
    """各站点综合水质类别分布（堆叠条形图）。"""
    plt = _plt()
    stations = list(distribution)
    labels = [station_names.get(s, s) for s in stations]

    fig, ax = plt.subplots(figsize=(8.4, 0.62 * len(stations) + 2.2), dpi=160)
    left = [0] * len(stations)
    for cls in CLASS_ORDER:
        values = [distribution[s].get(cls, 0) for s in stations]
        if not any(values):
            continue
        ax.barh(
            labels,
            values,
            left=left,
            color=CLASS_COLORS[cls],
            label=f"{cls}类",
            edgecolor="white",
            linewidth=0.9,
            height=0.58,
        )
        left = [a + b for a, b in zip(left, values)]

    ax.invert_yaxis()
    ax.set_xlabel("采样次数")
    ax.set_title("各站点综合水质类别分布", fontsize=12, fontweight="bold", pad=12)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(
        ncol=6,
        fontsize=8,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        frameon=False,
    )
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(out_path)


def pollution_timeline_chart(
    records: list[dict],
    station_names: dict[str, str],
    indicator_field: str,
    indicator_label: str,
    out_path: Path,
) -> str:
    """污染指数与关键指标的双子图时序对比。"""
    plt = _plt()
    import matplotlib.dates as mdates

    stations: dict[str, list[dict]] = {}
    for row in records:
        stations.setdefault(str(row.get("station_id", "")), []).append(row)

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(8.6, 6.4), dpi=160, sharex=True, gridspec_kw={"hspace": 0.28}
    )

    for i, (station, rows) in enumerate(stations.items()):
        rows.sort(key=lambda r: r["sample_time"])
        times = [r["sample_time"] for r in rows]
        index_values = [r.get("pollution_index") for r in rows]
        indicator_values = [r.get(indicator_field) for r in rows]
        color = STATION_COLORS[i % len(STATION_COLORS)]
        name = station_names.get(station, station)
        ax1.plot(times, index_values, color=color, linewidth=1.5, label=name)
        ax2.plot(times, indicator_values, color=color, linewidth=1.5, label=name)

    ax1.set_ylabel("内梅罗污染指数")
    ax1.set_title("污染指数与关键指标时序演变", fontsize=12, fontweight="bold", pad=12)
    ax1.axhline(1.0, color="#8E2B2B", linestyle="--", linewidth=1.0)
    ax1.text(0.01, 1.02, "III 类基准线", transform=ax1.get_yaxis_transform(),
             fontsize=8, color="#8E2B2B")
    ax2.set_ylabel(indicator_label)
    ax2.set_xlabel("采样时间")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax2.xaxis.set_major_locator(mdates.DayLocator(interval=4))

    for ax in (ax1, ax2):
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.grid(color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)

    # 顶部留出空间，避免图例压住数据曲线
    ax1.set_ylim(top=ax1.get_ylim()[1] * 1.22)
    ax1.legend(ncol=3, fontsize=8, frameon=False, loc="upper left")
    # 共享 x 轴的双子图用 subplots_adjust，tight_layout 与其不兼容会产生警告
    fig.subplots_adjust(left=0.085, right=0.985, top=0.925, bottom=0.095, hspace=0.22)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(out_path)


def exceed_rate_heatmap(
    exceed_rate: dict[str, dict[str, float]],
    station_names: dict[str, str],
    fields: list[str],
    field_labels: dict[str, str],
    out_path: Path,
) -> str:
    """站点 × 指标的劣于 III 类比例热力图。"""
    plt = _plt()

    stations = list(exceed_rate)
    data = [[exceed_rate[s].get(f, 0.0) * 100 for f in fields] for s in stations]
    rows = [station_names.get(s, s) for s in stations]
    cols = [field_labels.get(f, f) for f in fields]

    fig, ax = plt.subplots(
        figsize=(1.35 * len(cols) + 3.5, 0.7 * len(rows) + 2.0), dpi=160, layout="constrained"
    )
    image = ax.imshow(data, cmap="YlOrRd", aspect="auto", vmin=0)
    ax.set_xticks(range(len(cols)), cols, fontsize=9)
    ax.set_yticks(range(len(rows)), rows, fontsize=9)
    ax.set_title("各站点指标劣于 III 类的比例（%）", fontsize=12, fontweight="bold", pad=12)

    for i, row in enumerate(data):
        for j, value in enumerate(row):
            color = "white" if value > max(max(r) for r in data) * 0.6 else INK
            ax.text(j, i, f"{value:.1f}", ha="center", va="center", fontsize=9, color=color)

    bar = fig.colorbar(image, ax=ax, fraction=0.035, pad=0.02)
    bar.ax.tick_params(labelsize=8)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(out_path)


def stage_waterfall_chart(stages: list, out_path: Path) -> str:
    """各级过滤器耗时对比（性能实测数据的可视化证据）。"""
    plt = _plt()

    labels = [f"{s.index}. {s.label}" for s in stages]
    values = [s.elapsed_ms for s in stages]
    colors = ["#4CAF7D" if s.status == "OK" else "#D9534F" for s in stages]

    fig, ax = plt.subplots(figsize=(8.2, 0.55 * len(stages) + 1.9), dpi=160)
    bars = ax.barh(labels, values, color=colors, height=0.6)
    ax.invert_yaxis()

    for bar, value in zip(bars, values):
        ax.text(
            bar.get_width() + max(values) * 0.015,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.2f} ms",
            va="center",
            fontsize=9,
            color=INK,
        )

    ax.set_xlabel("执行耗时（毫秒）")
    ax.set_title("各级过滤器耗时分布", fontsize=12, fontweight="bold", pad=12)
    ax.set_xlim(0, max(values) * 1.18 if max(values) > 0 else 1)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(out_path)
