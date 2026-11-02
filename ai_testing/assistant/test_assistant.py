"""Offline tests for the pure assistant modules: python3 -m unittest ai_testing.assistant.test_assistant"""

import json
import unittest
from datetime import date

from ai_testing.assistant import catalog as cat
from ai_testing.assistant.evaluate import compare_plan, load_questions, summarize
from ai_testing.assistant.periods import (
	PeriodError,
	bucket_label,
	bucket_starts,
	previous_period,
	resolve_period,
)
from ai_testing.assistant.plan import PlanError, has_digits, parse_reply, validate_plan
from ai_testing.assistant.prompt import build_explain_messages, build_planner_messages
from ai_testing.assistant.results import NONE_KEY, OTHERS_KEY, build_answer, change_pct, choose_chart

TODAY = date(2026, 11, 2)  # a Monday
MONDAY, SATURDAY = 0, 5


class TestCatalog(unittest.TestCase):
	def test_builtin_catalog_is_valid(self):
		catalog = cat.build_catalog()
		self.assertIn("net_sales", catalog)
		self.assertEqual(catalog["item_sales_amount"]["table"], "Sales Invoice Item")
		self.assertEqual(catalog["net_sales"]["table"], "Sales Invoice")
		self.assertEqual(catalog["net_sales"]["dimensions"]["customer"]["label"], "Customer")

	def test_extra_metric_replaces_and_disable(self):
		extra = {**cat.BUILTIN_METRICS[0], "label": "Sales (custom)"}
		catalog = cat.build_catalog([extra], disabled={"stock_value"})
		self.assertEqual(catalog["net_sales"]["label"], "Sales (custom)")
		self.assertNotIn("stock_value", catalog)

	def test_invalid_metrics(self):
		base = {"key": "x", "label": "X", "description": "d", "doctype": "Item", "unit": "number", "dimensions": {}}
		with self.assertRaises(ValueError):
			cat.normalize_metric({**base, "aggregate": "avg"})
		with self.assertRaises(ValueError):
			cat.normalize_metric({**base, "aggregate": "sum"})  # no field
		with self.assertRaises(ValueError):
			cat.normalize_metric({**base, "aggregate": "count", "default_period": "this_month"})
		with self.assertRaises(ValueError):
			cat.normalize_metric({**base, "aggregate": "count", "dimensions": {"month": {"table": "Item", "field": "x"}}})
		with self.assertRaises(ValueError):
			cat.normalize_metric({**base, "aggregate": "count", "filters": [["Item", "x", "like", 1]]})

	def test_one_child_table_only(self):
		m = {
			"key": "x", "label": "X", "description": "d", "doctype": "Sales Invoice", "aggregate": "count",
			"unit": "number", "table": "Sales Invoice Item",
			"dimensions": {"a": {"table": "Sales Taxes and Charges", "field": "account_head"}},
		}
		with self.assertRaises(ValueError):
			cat.normalize_metric(m)


class TestPeriods(unittest.TestCase):
	def r(self, period, week_start=MONDAY):
		rng = resolve_period(period, TODAY, week_start)
		return rng["from"].isoformat(), rng["to"].isoformat()

	def test_presets(self):
		self.assertEqual(self.r("today"), ("2026-11-02", "2026-11-02"))
		self.assertEqual(self.r("yesterday"), ("2026-11-01", "2026-11-01"))
		self.assertEqual(self.r("this_month"), ("2026-11-01", "2026-11-30"))
		self.assertEqual(self.r({"preset": "last_month"}), ("2026-10-01", "2026-10-31"))
		self.assertEqual(self.r("this_quarter"), ("2026-10-01", "2026-12-31"))
		self.assertEqual(self.r("last_quarter"), ("2026-07-01", "2026-09-30"))
		self.assertEqual(self.r("last_year"), ("2025-01-01", "2025-12-31"))

	def test_weeks_follow_week_start(self):
		self.assertEqual(self.r("this_week", MONDAY), ("2026-11-02", "2026-11-08"))
		self.assertEqual(self.r("this_week", SATURDAY), ("2026-10-31", "2026-11-06"))
		self.assertEqual(self.r("last_week", SATURDAY), ("2026-10-24", "2026-10-30"))

	def test_january_last_month(self):
		rng = resolve_period("last_month", date(2026, 1, 15))
		self.assertEqual((rng["from"], rng["to"]), (date(2025, 12, 1), date(2025, 12, 31)))

	def test_named_and_custom(self):
		self.assertEqual(self.r({"month": "2024-02"}), ("2024-02-01", "2024-02-29"))
		self.assertEqual(self.r({"quarter": "2026-Q1"}), ("2026-01-01", "2026-03-31"))
		self.assertEqual(self.r({"year": "2025"}), ("2025-01-01", "2025-12-31"))
		self.assertEqual(self.r({"last_days": 7}), ("2026-10-27", "2026-11-02"))
		self.assertEqual(self.r({"from": "2026-01-05", "to": "2026-02-01"}), ("2026-01-05", "2026-02-01"))

	def test_none_and_errors(self):
		self.assertIsNone(resolve_period(None, TODAY))
		self.assertIsNone(resolve_period({}, TODAY))
		for bad in ("someday", {"last_days": 0}, {"month": "2026-13"}, {"from": "2026-02-01"},
				{"from": "2026-03-01", "to": "2026-02-01"}, {"from": "01/02/2026", "to": "2026-02-01"}, 5):
			with self.assertRaises(PeriodError, msg=bad):
				resolve_period(bad, TODAY)

	def test_previous_period(self):
		prev = previous_period(resolve_period("this_month", TODAY))
		self.assertEqual((prev["from"], prev["to"]), (date(2026, 10, 1), date(2026, 10, 31)))
		prev = previous_period(resolve_period("this_quarter", TODAY))
		self.assertEqual((prev["from"], prev["to"]), (date(2026, 7, 1), date(2026, 9, 30)))
		prev = previous_period(resolve_period({"month": "2026-03"}, TODAY))
		self.assertEqual((prev["from"], prev["to"]), (date(2026, 2, 1), date(2026, 2, 28)))
		prev = previous_period(resolve_period({"last_days": 7}, TODAY))
		self.assertEqual((prev["from"], prev["to"]), (date(2026, 10, 20), date(2026, 10, 26)))

	def test_buckets(self):
		starts = bucket_starts(date(2026, 1, 15), date(2026, 4, 2), "month")
		self.assertEqual([bucket_label(s, "month") for s in starts], ["2026-01", "2026-02", "2026-03", "2026-04"])
		starts = bucket_starts(date(2025, 11, 1), date(2026, 2, 1), "quarter")
		self.assertEqual([bucket_label(s, "quarter") for s in starts], ["2025-Q4", "2026-Q1"])
		starts = bucket_starts(date(2026, 11, 2), date(2026, 11, 9), "week", SATURDAY)
		self.assertEqual([s.isoformat() for s in starts], ["2026-10-31", "2026-11-07"])


class TestParseReply(unittest.TestCase):
	def test_fenced_and_prose(self):
		self.assertEqual(parse_reply('```json\n{"type": "query", "metric": "x"}\n```'), {"type": "query", "metric": "x"})
		self.assertEqual(parse_reply('Sure! {"type": "chat", "message": "hi"} done'), {"type": "chat", "message": "hi"})
		self.assertEqual(parse_reply('{bad} then {"a": 1}'), {"a": 1})

	def test_errors(self):
		for bad in ("", "   ", "no json here", "[1, 2]"):
			with self.assertRaises(PlanError):
				parse_reply(bad)

	def test_digits(self):
		self.assertTrue(has_digits("sales were 500"))
		self.assertTrue(has_digits("المبيعات ٥٠٠ ريال"))
		self.assertTrue(has_digits("۵۰۰"))
		self.assertFalse(has_digits("أهلا! أقدر أجاوب عن المبيعات"))


class TestValidatePlan(unittest.TestCase):
	def setUp(self):
		self.catalog = cat.build_catalog()

	def v(self, raw, week_start=MONDAY):
		return validate_plan(raw, self.catalog, TODAY, week_start)

	def test_default_period_and_defaults(self):
		spec = self.v({"type": "query", "metric": "net_sales"})
		self.assertEqual(spec["period"]["from"], "2026-11-01")
		self.assertEqual(spec["period_source"], "default")
		self.assertEqual((spec["top"], spec["sort"], spec["chart"]), (10, "desc", "auto"))
		self.assertIsNone(spec["group_kind"])
		json.dumps(spec)  # serialisable

	def test_balance_metric_has_no_default_period(self):
		spec = self.v({"metric": "receivables", "group_by": "customer"})
		self.assertIsNone(spec["period"])
		self.assertEqual(spec["group_kind"], "dimension")

	def test_period_ignored_for_stock(self):
		spec = self.v({"metric": "stock_qty", "period": {"preset": "last_month"}})
		self.assertIsNone(spec["period"])
		self.assertIn("period_ignored", spec["notes"])

	def test_group_by(self):
		self.assertEqual(self.v({"metric": "net_sales", "group_by": "Month"})["group_kind"], "date")
		with self.assertRaises(PlanError) as e:
			self.v({"metric": "net_sales", "group_by": "item"})
		self.assertEqual(e.exception.code, "bad_group_by")
		with self.assertRaises(PlanError):
			self.v({"metric": "stock_qty", "group_by": "month"})

	def test_unknown_metric(self):
		with self.assertRaises(PlanError) as e:
			self.v({"metric": "gross_profit"})
		self.assertEqual(e.exception.code, "unknown_metric")

	def test_filters(self):
		spec = self.v({"metric": "net_sales", "filters": {"Customer": " Ahmed ", "territory": ["Riyadh", "Riyadh", "Jeddah"]}})
		self.assertEqual(spec["filters"], {"customer": ["Ahmed"], "territory": ["Riyadh", "Jeddah"]})
		for bad in ({"item": "x"}, {"customer": ""}, {"customer": {"a": 1}}, ["customer"]):
			with self.assertRaises(PlanError, msg=bad):
				self.v({"metric": "net_sales", "filters": bad})

	def test_top_sort_chart_clamped(self):
		spec = self.v({"metric": "net_sales", "group_by": "customer", "top": 500, "sort": "up", "chart": "radar"})
		self.assertEqual((spec["top"], spec["sort"], spec["chart"]), (50, "desc", "auto"))
		self.assertEqual(self.v({"metric": "net_sales", "top": "abc"})["top"], 10)

	def test_compare(self):
		self.assertTrue(self.v({"metric": "net_sales", "compare_previous": True})["compare_previous"])
		spec = self.v({"metric": "receivables", "compare_previous": True})
		self.assertFalse(spec["compare_previous"])
		self.assertIn("compare_dropped", spec["notes"])
		spec = self.v({"metric": "net_sales", "group_by": "month", "compare_previous": True})
		self.assertFalse(spec["compare_previous"])

	def test_too_many_buckets(self):
		with self.assertRaises(PlanError) as e:
			self.v({"metric": "net_sales", "group_by": "day", "period": {"from": "2020-01-01", "to": "2026-01-01"}})
		self.assertEqual(e.exception.code, "too_many_buckets")

	def test_bad_period(self):
		with self.assertRaises(PlanError) as e:
			self.v({"metric": "net_sales", "period": {"preset": "next_century"}})
		self.assertEqual(e.exception.code, "bad_period")

	def test_unsupported_and_chat(self):
		self.assertEqual(self.v({"type": "unsupported", "reason": "No margin metric"})["type"], "unsupported")
		self.assertEqual(self.v({"type": "chat", "message": "أهلا بيك"})["type"], "chat")
		spec = self.v({"type": "chat", "message": "Your sales were 12,500 this month"})
		self.assertEqual(spec["type"], "unsupported")
		self.assertIn("chat_with_numbers", spec["notes"])
		with self.assertRaises(PlanError):
			self.v({"type": "delete"})

	def test_spec_round_trip(self):
		spec = self.v({"metric": "net_sales", "group_by": "customer", "period": {"from": "2026-01-01", "to": "2026-01-31"}})
		again = self.v(spec)
		self.assertEqual(again["period"]["from"], "2026-01-01")
		self.assertEqual(again["filters"], spec["filters"])


class TestResults(unittest.TestCase):
	def setUp(self):
		self.catalog = cat.build_catalog()

	def spec(self, **kw):
		base = {"metric": "net_sales", "group_by": None, "group_kind": None, "period": None, "filters": {},
			"top": 3, "sort": "desc", "chart": "auto", "compare_previous": False}
		base.update(kw)
		return base

	def test_total_and_sign(self):
		ans = build_answer(self.spec(), self.catalog["sales_returns"], -1250.456)
		self.assertEqual(ans["total"], 1250.46)
		self.assertIsNone(ans["chart"])
		ans = build_answer(self.spec(compare_previous=True), self.catalog["net_sales"], 120, 100)
		self.assertEqual((ans["previous_total"], ans["change_pct"]), (100, 20.0))

	def test_counts_are_ints(self):
		ans = build_answer(self.spec(), self.catalog["sales_invoice_count"], 7.0)
		self.assertEqual(ans["total"], 7)
		self.assertIsInstance(ans["total"], int)

	def test_dimension_top_others_labels(self):
		rows = [("C1", 500), ("C2", 300), (None, 50), ("C3", 200), ("C4", 100), ("C5", 0)]
		ans = build_answer(self.spec(group_by="customer", group_kind="dimension"), self.catalog["net_sales"], rows,
			labels={"C1": "Customer One"})
		self.assertEqual([r["key"] for r in ans["rows"]], ["C1", "C2", "C3"])
		self.assertEqual(ans["rows"][0]["label"], "Customer One")
		self.assertEqual(ans["rows"][1]["label"], "C2")
		self.assertEqual(ans["others"]["count"], 2)  # C4 and the empty key; C5 (zero) is dropped
		self.assertEqual(ans["others"]["value"], 150)
		self.assertEqual(ans["total"], 1150)
		self.assertEqual(ans["chart"]["type"], "donut")
		self.assertEqual(ans["chart"]["labels"][-1], OTHERS_KEY)

	def test_none_key_and_ascending(self):
		rows = [("C1", 500), (None, 50)]
		ans = build_answer(self.spec(group_by="customer", group_kind="dimension", sort="asc"), self.catalog["net_sales"], rows)
		self.assertEqual(ans["rows"][0]["key"], NONE_KEY)
		self.assertIsNone(ans["rows"][0]["label"])
		self.assertEqual(ans["chart"]["labels"][0], NONE_KEY)

	def test_dimension_compare(self):
		spec = self.spec(group_by="customer", group_kind="dimension", compare_previous=True)
		ans = build_answer(spec, self.catalog["net_sales"], [("A", 150), ("B", 50)], [("A", 100), ("C", 80)])
		by_key = {r["key"]: r for r in ans["rows"]}
		self.assertEqual(by_key["A"]["change_pct"], 50.0)
		self.assertEqual(by_key["C"]["value"], 0)
		self.assertEqual(by_key["C"]["previous"], 80)
		self.assertEqual(ans["previous_total"], 180)
		self.assertEqual(ans["chart"]["type"], "bar")
		self.assertEqual(len(ans["chart"]["datasets"]), 2)

	def test_date_buckets_zero_filled(self):
		spec = self.spec(group_by="month", group_kind="date",
			period={"from": "2026-01-01", "to": "2026-04-30", "preset": None, "label": None})
		rows = [("2026-01-03", 10), ("2026-01-20", 5), (date(2026, 3, 9), 7)]
		ans = build_answer(spec, self.catalog["net_sales"], rows)
		self.assertEqual([(r["label"], r["value"]) for r in ans["rows"]],
			[("2026-01", 15), ("2026-02", 0), ("2026-03", 7), ("2026-04", 0)])
		self.assertEqual(ans["total"], 22)
		self.assertEqual(ans["chart"]["type"], "line")

	def test_date_buckets_without_period(self):
		spec = self.spec(group_by="year", group_kind="date")
		ans = build_answer(spec, self.catalog["net_sales"], [("2024-05-01", 1), ("2026-01-01", 2)])
		self.assertEqual([r["label"] for r in ans["rows"]], ["2024", "2025", "2026"])
		self.assertEqual(build_answer(spec, self.catalog["net_sales"], [])["rows"], [])

	def test_chart_choice(self):
		rows = [{"value": 1}, {"value": 2}]
		self.assertEqual(choose_chart({"chart": "pie"}, "dimension", rows, False), "pie")
		self.assertEqual(choose_chart({"chart": "pie"}, "dimension", [{"value": -1}, {"value": 2}], False), "bar")
		self.assertEqual(choose_chart({"chart": "auto"}, "dimension", [{"value": 1}] * 8, False), "bar")
		self.assertIsNone(choose_chart({"chart": "none"}, "dimension", rows, False))
		self.assertIsNone(choose_chart({"chart": "auto"}, "dimension", [{"value": 1}], False))
		self.assertEqual(choose_chart({"chart": "bar"}, "date", rows, False), "bar")
		self.assertEqual(choose_chart({"chart": "auto"}, "date", rows, False), "bar")

	def test_change_pct(self):
		self.assertIsNone(change_pct(10, 0))
		self.assertEqual(change_pct(50, -100), 150.0)


class TestPrompt(unittest.TestCase):
	def test_planner_messages(self):
		catalog = cat.build_catalog()
		prev_spec = validate_plan({"metric": "net_sales", "group_by": "customer"}, catalog, TODAY)
		msgs = build_planner_messages("وبالشهر؟", catalog, TODAY, {"question": "sales by customer", "plan": prev_spec}, "Acme")
		self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "user"])
		self.assertIn("net_sales", msgs[0]["content"])
		self.assertIn("2026-11-02", msgs[0]["content"])
		self.assertIn("Acme", msgs[0]["content"])
		history = json.loads(msgs[2]["content"])
		self.assertIsNone(history["period"])  # default period is not echoed back
		self.assertEqual(msgs[3]["content"], "وبالشهر؟")

	def test_explain_messages(self):
		metric = cat.build_catalog()["net_sales"]
		msgs = build_explain_messages("sales?", metric, {"total": 5})
		self.assertIn("Net Sales", msgs[1]["content"])


class TestEvaluate(unittest.TestCase):
	def test_questions_file_and_specs(self):
		catalog = cat.build_catalog()
		questions = load_questions()
		self.assertGreaterEqual(len(questions), 30)
		for q in questions:
			exp = q["expected"]
			if exp.get("metric"):
				self.assertIn(exp["metric"], catalog, q["question"])

	def test_compare(self):
		catalog = cat.build_catalog()
		spec = validate_plan({"metric": "net_sales", "group_by": "customer", "period": "last_month",
			"filters": {"territory": "Riyadh"}}, catalog, TODAY)
		self.assertEqual(compare_plan({"metric": "net_sales", "group_by": "customer", "period": "last_month",
			"filters": {"territory": "riyadh"}}, spec), [])
		problems = compare_plan({"metric": "net_sales", "period": "this_month", "filters": {}}, spec)
		self.assertEqual(len(problems), 2)
		self.assertEqual(compare_plan({"type": "unsupported"}, {"type": "unsupported"}), [])
		self.assertTrue(compare_plan({"type": "unsupported"}, spec))
		default = validate_plan({"metric": "net_sales"}, catalog, TODAY)
		self.assertEqual(compare_plan({"period": "this_month", "group_by": None}, default), [])
		self.assertEqual(compare_plan({"period": None}, validate_plan({"metric": "receivables"}, catalog, TODAY)), [])

	def test_summarize(self):
		s = summarize([{"problems": []}, {"problems": ["x"]}, {"problems": [], "error": "boom"}])
		self.assertEqual((s["total"], s["passed"], s["score_pct"], len(s["failures"])), (3, 1, 33.3, 2))


if __name__ == "__main__":
	unittest.main()
