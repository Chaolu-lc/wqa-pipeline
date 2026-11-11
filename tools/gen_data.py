"""模拟监测数据生成脚本（一次性工具，不参与管线运行）。

生成 3 个断面 × 30 天 × 4 次/日 = 360 条原始记录，并**刻意植入**四类数据问题，
用于验证下游各级过滤器的处理能力：

    1. 缺失值——模拟探头离线导致的部分时段无读数；
    2. 类型错误与量程越界——模拟通讯乱码（N/A）与单位错乱（负值、异常放大）；
    3. 重复上报——模拟采集端重复写入；
    4. 时间格式不统一——两套格式混用，检验校验过滤器的时间解析能力。

另外植入两类**真实业务事件**，用于验证"采集异常"与"污染事件"的区分逻辑：

    - 中游断面第 12~15 天：偷排事件，氨氮/COD/总磷/总氮同时升高、溶解氧下降；
    - 下游断面第 22 天 14 时：单指标瞬时突变，模拟探头故障。

数据全部由固定随机种子生成，可重复复现，不依赖任何外部数据源。
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

STATIONS = [
    {
        "id": "ST-001",
        "name": "上游对照断面",
        "base": {
            "water_temp": 19.8, "ph": 7.55, "do": 8.4, "cod": 9.5,
            "nh3n": 0.12, "tp": 0.025, "tn": 0.30, "turbidity": 13.0,
        },
    },
    {
        "id": "ST-002",
        "name": "城镇中游断面",
        "base": {
            "water_temp": 20.4, "ph": 7.35, "do": 6.3, "cod": 16.5,
            "nh3n": 0.62, "tp": 0.11, "tn": 0.95, "turbidity": 37.0,
        },
    },
    {
        "id": "ST-003",
        "name": "下游工业区断面",
        "base": {
            "water_temp": 21.1, "ph": 7.05, "do": 4.5, "cod": 26.0,
            "nh3n": 1.15, "tp": 0.23, "tn": 1.45, "turbidity": 61.0,
        },
    },
]

FIELDS = ["water_temp", "ph", "do", "cod", "nh3n", "tp", "tn", "turbidity"]
NOISE = {
    "water_temp": 0.35, "ph": 0.07, "do": 0.28, "cod": 1.1,
    "nh3n": 0.045, "tp": 0.011, "tn": 0.085, "turbidity": 3.8,
}
SAMPLE_HOURS = (2, 8, 14, 20)


def build_rows(seed: int, days: int, start: datetime) -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []

    for station in STATIONS:
        for day in range(days):
            for hour in SAMPLE_HOURS:
                moment = start + timedelta(days=day, hours=hour)
                diurnal = math.sin((hour - 6) / 24 * 2 * math.pi)
                values: dict[str, float] = {}

                for field in FIELDS:
                    value = station["base"][field]
                    if field == "water_temp":
                        value += 1.1 * diurnal - 0.055 * day  # 日周期 + 秋季缓慢降温
                    elif field == "do":
                        value -= 0.45 * diurnal  # 午后光合作用强、溶解氧偏高
                    value += rng.gauss(0, NOISE[field])
                    values[field] = max(0.0, value)

                values["ph"] = min(9.0, max(6.2, values["ph"]))

                # 事件一：中游断面偷排，多指标同时恶化
                if station["id"] == "ST-002" and 12 <= day <= 15:
                    values["nh3n"] *= 2.9
                    values["cod"] *= 2.0
                    values["tp"] *= 1.9
                    values["tn"] *= 1.6
                    values["do"] *= 0.72

                # 事件二：下游断面探头故障，单指标瞬时突变
                if station["id"] == "ST-003" and day == 22 and hour == 14:
                    values["cod"] = 118.0

                rows.append(
                    {
                        "station_id": station["id"],
                        "station_name": station["name"],
                        "dt": moment,
                        **{field: round(values[field], 3) for field in FIELDS},
                    }
                )
    return rows


def inject_dirty(rows: list[dict], seed: int) -> dict[str, int]:
    """植入四类数据问题，返回植入数量用于核对。"""
    rng = random.Random(seed + 1)
    counts = {"missing": 0, "type_error": 0, "range_error": 0, "duplicate": 0}

    # 事件行不做破坏，保证业务信号可被下游正确识别
    # （起始日为 10-01，故"第 n 天"对应 dt.day == n + 1）
    def is_protected(row: dict) -> bool:
        if row["station_id"] == "ST-002" and 13 <= row["dt"].day <= 16:
            return True  # 偷排事件期间
        if row["station_id"] == "ST-003" and row["dt"].day == 23 and row["dt"].hour == 14:
            return True  # 探头故障时刻
        return False

    protected = {index for index, row in enumerate(rows) if is_protected(row)}

    for index, row in enumerate(rows):
        if index in protected:
            continue
        roll = rng.random()
        field = rng.choice(FIELDS)
        if roll < 0.045:  # 4.5% 缺失
            row[field] = ""
            counts["missing"] += 1
        elif roll < 0.06:  # 1.5% 类型错误
            row[field] = rng.choice(["N/A", "--", "null"])
            counts["type_error"] += 1
        elif roll < 0.072:  # 1.2% 量程越界
            # 水温的合理下限是 -5，负值仍属合法，故改用明显偏高的值
            row[field] = 60.0 if field == "water_temp" else rng.choice([-1.0, -0.5, 999.0])
            counts["range_error"] += 1

    duplicates = [dict(rng.choice(rows)) for _ in range(4)]
    rows.extend(duplicates)
    counts["duplicate"] = len(duplicates)
    return counts


def write_csv(rows: list[dict], out_dir: Path, start: datetime) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    columns = ["station_id", "station_name", "sample_time", *FIELDS]

    for station in STATIONS:
        subset = [row for row in rows if row["station_id"] == station["id"]]
        path = out_dir / f"{start:%Y-%m}_{station['id']}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(columns)
            for i, row in enumerate(subset):
                # 两套时间格式混用，模拟不同批次设备上报格式不一致
                fmt = "%Y-%m-%d %H:%M:%S" if i % 3 else "%Y/%m/%d %H:%M"
                writer.writerow(
                    [row["station_id"], row["station_name"], row["dt"].strftime(fmt)]
                    + [row[field] for field in FIELDS]
                )
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="生成模拟水质监测数据")
    parser.add_argument("--out", default="data/raw", help="输出目录")
    parser.add_argument("--days", type=int, default=30, help="监测天数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--start", default="2026-10-01T00:00:00", help="起始时间")
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start)
    rows = build_rows(args.seed, args.days, start)
    injected = inject_dirty(rows, args.seed)
    files = write_csv(rows, Path(args.out), start)

    print(f"[gen] 生成 {len(files)} 个文件，共 {len(rows)} 行，起始 {start:%Y-%m-%d}")
    print(f"[gen] 植入数据问题：缺失 {injected['missing']}、类型错误 {injected['type_error']}、"
          f"量程越界 {injected['range_error']}、重复 {injected['duplicate']}")
    for path in files:
        print(f"[gen]   {path}  {path.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
