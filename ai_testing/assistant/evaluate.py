"""Compare validated plans (specs) with expected plans. Pure module (no frappe import).

An expected plan lists only what matters for the question, e.g.
	{"type": "query", "metric": "net_sales", "group_by": "customer", "period": "last_month"}
Keys left out are not checked. "period" is a preset name, or a dict matched on its keys.
"filters" maps a dimension to a lower-case text the value must contain ("" = any value).
"""

from __future__ import annotations

import json
from pathlib import Path

QUESTIONS_FILE = Path(__file__).with_name("eval_questions.json")


def load_questions(path: str | Path | None = None) -> list[dict]:
	with open(path or QUESTIONS_FILE, encoding="utf-8") as f:
		return json.load(f)


def _period_matches(expected, actual_raw) -> bool:
	actual = actual_raw or None
	if isinstance(actual, str):
		actual = {"preset": actual}
	if expected is None:
		return actual is None
	if isinstance(expected, str):
		return bool(actual) and str(actual.get("preset") or "").lower() == expected
	if not actual:
		return False
	return all(str(actual.get(k)) == str(v) for k, v in expected.items())


def compare_plan(expected: dict, actual: dict) -> list[str]:
	"""Differences between an expected plan and a validated spec (empty = pass)."""
	problems = []
	actual = actual or {}
	exp_type = expected.get("type", "query")
	act_type = str(actual.get("type") or "query").lower()
	if exp_type != act_type:
		return [f"type: expected {exp_type}, got {act_type}"]
	if exp_type != "query":
		return problems

	for key in ("metric", "group_by", "sort", "top"):
		if key in expected and expected[key] != (actual.get(key) or None):
			problems.append(f"{key}: expected {expected[key]!r}, got {actual.get(key)!r}")

	if "period" in expected and not _period_matches(expected["period"], actual.get("period")):
		problems.append(f"period: expected {expected['period']!r}, got {actual.get('period')!r}")

	if "compare_previous" in expected and bool(expected["compare_previous"]) != bool(actual.get("compare_previous")):
		problems.append(f"compare_previous: expected {expected['compare_previous']}")

	if "filters" in expected:
		act_filters = {str(k).lower(): v for k, v in (actual.get("filters") or {}).items()}
		for key, needle in expected["filters"].items():
			if key not in act_filters:
				problems.append(f"filters: missing {key}")
				continue
			values = act_filters[key] if isinstance(act_filters[key], list) else [act_filters[key]]
			if needle and not any(needle in str(v).lower() for v in values):
				problems.append(f"filters.{key}: expected a value containing {needle!r}, got {values!r}")
		extra = set(act_filters) - set(expected["filters"])
		if extra:
			problems.append(f"filters: unexpected {', '.join(sorted(extra))}")
	return problems


def summarize(results: list[dict]) -> dict:
	"""results: [{"question", "problems", "error"}] -> totals and failures."""
	passed = [r for r in results if not r.get("problems") and not r.get("error")]
	failed = [r for r in results if r not in passed]
	total = len(results)
	return {
		"total": total,
		"passed": len(passed),
		"score_pct": round(len(passed) / total * 100, 1) if total else 0.0,
		"failures": failed,
	}
