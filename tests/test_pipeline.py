"""过滤器与引擎的单元测试（仅使用标准库 unittest，无需额外安装）。

每个过滤器都可以脱离整条管线独立测试——这正是管道-过滤器架构对可测试性的直接贡献：
过滤器之间没有隐式依赖，测试用例只需要构造一个 ``FilterContext`` 即可。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.base import Filter, FilterContext  # noqa: E402
from pipeline.engine import Pipeline, create_filter, discover_filters, register  # noqa: E402
from pipeline.standards import assess, classify, load_standard  # noqa: E402

STANDARD_PATH = Path(__file__).resolve().parent.parent / "config" / "water_quality_standard.yaml"

NUMERIC = ["water_temp", "ph", "do", "cod", "nh3n", "tp", "tn", "turbidity"]
REQUIRED = ["station_id", "station_name", "sample_time", "ph", "do", "cod", "nh3n", "tp", "tn"]
TIME_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"]


def make_record(**overrides) -> dict:
    base = {
        "station_id": "S1",
        "station_name": "测试断面",
        "sample_time": "2026-10-01 02:00:00",
        "water_temp": "20.0",
        "ph": "7.2",
        "do": "8.1",
        "cod": "10.0",
        "nh3n": "0.10",
        "tp": "0.02",
        "tn": "0.30",
        "turbidity": "12.0",
    }
    base.update(overrides)
    return base


class TestValidateFilter(unittest.TestCase):
    def setUp(self):
        discover_filters()
        self.filter = create_filter(
            "validate",
            {
                "required_fields": REQUIRED,
                "numeric_fields": NUMERIC,
                "time_formats": TIME_FORMATS,
                "physical_ranges": {"ph": [0, 14]},
            },
        )

    def test_drops_bad_time_and_duplicates(self):
        records = [
            make_record(),
            make_record(),  # 重复
            make_record(sample_time="不是时间"),  # 时间无法解析
            make_record(sample_time="2026/10/01 06:00", ph="9.9"),  # 合法，不同时间格式
        ]
        ctx = self.filter.process(FilterContext(records=records))
        self.assertEqual(ctx.row_count, 2)
        self.assertEqual(ctx.meta["validate"]["duplicate"], 1)
        self.assertEqual(ctx.meta["validate"]["time_error"], 1)

    def test_range_violation_becomes_none(self):
        ctx = self.filter.process(FilterContext(records=[make_record(ph="99")]))
        self.assertIsNone(ctx.records[0]["ph"])
        self.assertEqual(ctx.meta["validate"]["range_error"], 1)

    def test_missing_required_field_raises(self):
        bad = make_record()
        bad.pop("cod")
        with self.assertRaises(Exception):
            self.filter.process(FilterContext(records=[bad]))


class TestMissingFilter(unittest.TestCase):
    def test_linear_interpolation(self):
        records = [
            {"station_id": "S1", "sample_time": datetime(2026, 10, 1, h), "v": v}
            for h, v in enumerate([1.0, None, None, 4.0])
        ]
        out = create_filter("missing", {"strategy": "linear", "fields": ["v"]}).process(
            FilterContext(records=records)
        )
        self.assertEqual([row["v"] for row in out.records], [1.0, 2.0, 3.0, 4.0])

    def test_mean_strategy(self):
        records = [
            {"station_id": "S1", "sample_time": datetime(2026, 10, 1, h), "v": v}
            for h, v in enumerate([2.0, None, 4.0])
        ]
        out = create_filter("missing", {"strategy": "mean", "fields": ["v"]}).process(
            FilterContext(records=records)
        )
        self.assertEqual(out.records[1]["v"], 3.0)

    def test_abandons_when_missing_ratio_too_high(self):
        records = [
            {"station_id": "S1", "sample_time": datetime(2026, 10, 1, h), "v": v}
            for h, v in enumerate([1.0, None, None, None])
        ]
        out = create_filter(
            "missing", {"strategy": "linear", "fields": ["v"], "max_fill_ratio": 0.5}
        ).process(FilterContext(records=records))
        self.assertIn("S1.v", out.meta["missing"]["abandoned"])


class TestOutlierFilter(unittest.TestCase):
    def _records(self, extra: dict | None = None, count: int = 20) -> list[dict]:
        records = []
        for i in range(count):
            records.append(
                {
                    "station_id": "S1",
                    "sample_time": datetime(2026, 10, 1 + i // 4, i % 4),
                    "cod": 10.0 + (i % 5),
                    "nh3n": 0.10 + 0.01 * (i % 4),
                }
            )
        if extra:
            records.append(
                {
                    "station_id": "S1",
                    "sample_time": datetime(2026, 10, 20, 0),
                    **{k: v for k, v in extra.items()},
                }
            )
        return records

    def test_single_indicator_outlier_is_clipped(self):
        ctx = create_filter(
            "outlier",
            {"method": "iqr", "k": 3.0, "action": "clip", "corroboration": 2,
             "fields": ["cod", "nh3n"]},
        ).process(FilterContext(records=self._records({"cod": 500.0, "nh3n": 0.12})))
        self.assertLess(ctx.records[-1]["cod"], 500.0)
        self.assertGreaterEqual(ctx.meta["outlier"]["count"], 1)

    def test_multi_indicator_outlier_is_preserved(self):
        ctx = create_filter(
            "outlier",
            {"method": "iqr", "k": 3.0, "action": "clip", "corroboration": 2,
             "fields": ["cod", "nh3n"]},
        ).process(FilterContext(records=self._records({"cod": 500.0, "nh3n": 9.0})))
        self.assertEqual(ctx.records[-1]["cod"], 500.0)
        self.assertTrue(ctx.records[-1].get("_event_suspected"))
        self.assertEqual(ctx.meta["outlier"]["event_samples"], 1)


class TestStandards(unittest.TestCase):
    def setUp(self):
        self.standard = load_standard(STANDARD_PATH)

    def test_classification_boundaries(self):
        indicators = self.standard["indicators"]
        self.assertEqual(classify("cod", 15, indicators), "I")
        self.assertEqual(classify("cod", 20, indicators), "III")
        self.assertEqual(classify("cod", 25, indicators), "IV")
        self.assertEqual(classify("cod", 99, indicators), "劣V")

    def test_dissolved_oxygen_is_higher_better(self):
        indicators = self.standard["indicators"]
        self.assertEqual(classify("do", 9.0, indicators), "I")
        self.assertEqual(classify("do", 2.0, indicators), "V")
        self.assertEqual(classify("do", 1.0, indicators), "劣V")

    def test_worst_indicator_dominates(self):
        result = assess(
            {"do": 8.0, "cod": 10.0, "nh3n": 0.9, "tp": 0.02, "tn": 0.3}, self.standard
        )
        self.assertEqual(result["wq_class"], "III")
        self.assertEqual(result["exceed_items"], [])

        result = assess(
            {"do": 8.0, "cod": 10.0, "nh3n": 1.8, "tp": 0.02, "tn": 0.3}, self.standard
        )
        self.assertEqual(result["wq_class"], "V")
        self.assertIn("nh3n", result["exceed_items"])


class _BoomFilter(Filter):
    """测试专用：一个必定失败的过滤器，用于验证引擎的异常隔离。"""

    name = "boom_test"
    label = "故障过滤器"

    def process(self, ctx):
        raise RuntimeError("故意失败")


class TestEngine(unittest.TestCase):
    def setUp(self):
        register(_BoomFilter)
        self.ctx = FilterContext(
            records=[{"station_id": "S1", "sample_time": datetime(2026, 10, 1, 0),
                      "v": 1.0}]
        )

    def test_skip_isolates_failure_and_continues(self):
        result = Pipeline([("boom_test", {}), ("missing", {"fields": ["v"]})], on_error="skip").run(
            self.ctx
        )
        self.assertEqual(result.stages[0].status, "ERROR")
        self.assertEqual(result.stages[1].status, "OK")
        self.assertEqual(result.status, "DEGRADED")

    def test_abort_stops_downstream(self):
        result = Pipeline(
            [("boom_test", {}), ("missing", {"fields": ["v"]})], on_error="abort"
        ).run(self.ctx)
        self.assertEqual(result.stages[1].status, "SKIPPED")

    def test_trace_records_row_counts(self):
        result = Pipeline([("missing", {"fields": ["v"]})]).run(self.ctx)
        stage = result.stages[0]
        self.assertEqual(stage.rows_in, 1)
        self.assertEqual(stage.rows_out, 1)
        self.assertGreaterEqual(stage.elapsed_ms, 0)


class TestIntegration(unittest.TestCase):
    """端到端：从 CSV 到报告，验证七级过滤链整体可用。"""

    def test_full_pipeline_on_generated_file(self):
        discover_filters()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "raw"
            data_dir.mkdir()
            csv_path = data_dir / "sample.csv"
            header = ["station_id", "station_name", "sample_time", *NUMERIC]
            rows = []
            for i in range(24):
                rows.append(
                    ["S1", "测试断面", f"2026-10-{1 + i // 4:02d} {2 + 6 * (i % 4):02d}:00:00",
                     "20.0", "7.2", "8.1", "10.0", "0.10", "0.02", "0.30", "12.0"]
                )
            csv_path.write_text(
                "\n".join([",".join(header)] + [",".join(r) for r in rows]), encoding="utf-8"
            )

            stages = [
                ("loader", {"patterns": [str(data_dir / "*.csv")]}),
                ("validate", {"required_fields": REQUIRED, "numeric_fields": NUMERIC,
                              "time_formats": TIME_FORMATS}),
                ("missing", {"strategy": "linear", "fields": ["cod"]}),
                ("outlier", {"method": "iqr", "action": "clip", "fields": ["cod"]}),
                ("index", {"standard": str(STANDARD_PATH)}),
                ("trend", {"consecutive": 3, "warn_class": "IV"}),
                ("report", {"output_dir": str(Path(tmp) / "out"), "formats": ["json", "md"],
                            "charts": False}),
            ]
            result = Pipeline(stages).run(FilterContext())
            self.assertEqual(result.status, "OK")
            self.assertEqual(result.context.row_count, 24)
            self.assertEqual(len(result.stages), 7)
            self.assertTrue((Path(tmp) / "out" / "report.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
