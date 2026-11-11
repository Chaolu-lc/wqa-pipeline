"""水质评价的标准加载与计算（不含 IO、不含清洗，纯函数）。

标准限值本身外置在 ``config/water_quality_standard.yaml``，不写死在代码里——
这是"配置驱动"的直接体现：把河流标准换成湖库标准，只需要换一个 yaml，
不需要改动任何一行 Python 代码。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

CLASS_ORDER = ["I", "II", "III", "IV", "V"]
WORST_CLASS = "劣V"


def load_standard(path: str | Path) -> dict[str, Any]:
    """加载标准配置。返回结构中 ``indicators`` 为各指标的限值定义。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"标准配置文件不存在：{path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "indicators" not in data:
        raise ValueError("标准配置格式错误：缺少 indicators 节点")
    return data


def _values(limit_cfg: dict[str, Any]) -> list[float]:
    vals = [float(x) for x in limit_cfg["limits"]]
    if len(vals) != 5:
        raise ValueError(f"指标 {limit_cfg.get('field')} 的限值应为 5 档（I~V 类）")
    return vals


def classify(field: str, value: float | None, indicators: dict[str, Any]) -> str | None:
    """单因子评价：确定该指标所属的水质类别（I/II/III/IV/V/劣V）。

    ``direction`` 为 ``lower`` 表示"越小越好"（如 COD、氨氮），
    ``higher`` 表示"越大越好"（如溶解氧）。
    """
    if value is None:
        return None
    cfg = indicators.get(field)
    if not cfg:
        return None
    limits = _values(cfg)
    if cfg.get("direction", "lower") == "higher":
        for cls, bound in zip(CLASS_ORDER, limits):
            if value >= bound:
                return cls
        return WORST_CLASS
    for cls, bound in zip(CLASS_ORDER, limits):
        if value <= bound:
            return cls
    return WORST_CLASS


def _ratio(field: str, value: float | None, indicators: dict[str, Any]) -> float | None:
    """计算分指数 P_i = C_i / S_i（S_i 取 III 类限值作为基准）。

    溶解氧为"越大越好"，采用倒数形式 P = S / C，使其量纲与其他指标一致。
    这是工程上常用的简化处理，报告中已注明。
    """
    if value is None:
        return None
    cfg = indicators.get(field)
    if not cfg:
        return None
    s3 = _values(cfg)[2]  # III 类限值作为评价基准
    if cfg.get("direction", "lower") == "higher":
        if value <= 0:
            return 99.0
        return s3 / value
    return value / s3 if s3 else None


def nemerow_index(ratios: list[float]) -> float:
    """内梅罗污染指数：兼顾最差指标与整体水平，避免均值掩盖单点超标。"""
    vals = [r for r in ratios if r is not None]
    if not vals:
        return 0.0
    avg = sum(vals) / len(vals)
    worst = max(vals)
    return math.sqrt((avg**2 + worst**2) / 2)


def assess(record: dict[str, Any], standard: dict[str, Any]) -> dict[str, Any]:
    """对一条监测记录做综合水质评价。

    综合类别取各单因子类别中最差的一项（单因子评价法，GB 3838-2002 通行做法），
    这样任何一个指标超标都不会被平均值掩盖。
    """
    indicators: dict[str, Any] = standard["indicators"]
    field_classes: dict[str, str] = {}
    ratios: list[float] = []
    exceed: list[str] = []

    for field in indicators:
        cls = classify(field, record.get(field), indicators)
        if cls is None:
            continue
        field_classes[field] = cls
        ratio = _ratio(field, record.get(field), indicators)
        if ratio is not None:
            ratios.append(ratio)
        if cls not in ("I", "II", "III"):
            exceed.append(field)

    if field_classes:
        worst = max(
            field_classes.values(),
            key=lambda c: CLASS_ORDER.index(c) if c in CLASS_ORDER else len(CLASS_ORDER),
        )
    else:
        worst = None

    return {
        "field_classes": field_classes,
        "wq_class": worst,
        "pollution_index": round(nemerow_index(ratios), 3),
        "exceed_items": exceed,
    }


def class_rank(cls: str | None) -> int:
    """把类别转成可比较的序号：I=1 … V=5，劣V=6。"""
    if cls is None:
        return 0
    return CLASS_ORDER.index(cls) + 1 if cls in CLASS_ORDER else len(CLASS_ORDER) + 1
