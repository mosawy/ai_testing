"""Prompts for the planner and the optional explanation. Pure module (no frappe import)."""

from __future__ import annotations

import json
from datetime import date

from ai_testing.assistant.catalog import DATE_GROUPS
from ai_testing.assistant.periods import PRESETS

MAX_HISTORY_QUESTION = 300


def describe_catalog(catalog: dict) -> str:
	lines = []
	for m in catalog.values():
		dims = ", ".join(m["dimensions"]) or "none"
		if m["date_field"]:
			dates = f"has dates (default period: {m['default_period'] or 'all dates'})"
		else:
			dates = "no dates (current balance)"
		lines.append(f"- {m['key']}: {m['label']}. {m['description']} Dimensions: {dims}. {dates}.")
		if m["examples"]:
			lines.append(f"  Example questions: {' | '.join(m['examples'])}")
	return "\n".join(lines)


PLAN_FORMAT = """\
Answer with ONE JSON object and nothing else. Three shapes are allowed:

1) A data question the catalog covers:
{"type": "query", "metric": "<metric key>", "group_by": null | "<dimension>" | "day" | "week" | "month" | "quarter" | "year",
 "period": null | {"preset": "<preset>"} | {"last_days": 30} | {"month": "2026-03"} | {"quarter": "2026-Q1"} | {"year": 2025} | {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
 "filters": {"<dimension>": "<value as the user wrote it>" | ["<value>", "..."]},
 "top": 10, "sort": "desc" | "asc", "chart": "auto" | "bar" | "line" | "pie" | "donut" | "number" | "none",
 "compare_previous": false}

2) A data question the catalog does NOT cover (another measure, a calculation across metrics,
   a list of individual documents, anything you would have to guess):
{"type": "unsupported", "reason": "<one short sentence in the user's language>"}

3) A greeting or a question about what you can do:
{"type": "chat", "message": "<short reply in the user's language, no numbers>"}
"""

RULES = """\
Rules:
- You never compute or state numbers. The server runs the query and shows the result.
- Pick exactly one metric from the catalog. Never invent metric keys or dimensions.
- group_by and filter keys must be dimensions listed for that metric (or a date grouping for metrics with dates).
- Use a preset for relative periods (this month, last year, ...). Use month/quarter/year/from-to for named periods.
  Leave period null when the user names none: the metric's default applies.
- Put names exactly as the user wrote them in filters (customer, item, warehouse, ...). The server finds the record.
- "top N" / "highest" / "lowest" -> group_by a dimension, top N, sort desc or asc.
- "trend", "by month", "monthly", "over time" -> group_by a date grouping.
- "compared to last month/year", "vs previous" -> compare_previous true (not with a date grouping).
- If a question is a follow-up ("and by month?", "same for last year", "only Riyadh"), change the previous plan.
- If you are not sure which metric fits, answer "unsupported" rather than guessing.
"""


def build_planner_messages(
	question: str,
	catalog: dict,
	today: date,
	previous: dict | None = None,
	company: str | None = None,
) -> list[dict]:
	"""Messages that ask the model for a plan."""
	system = "\n".join(
		[
			"You turn business questions about an ERPNext system into a query plan.",
			"The user may write in Arabic (including Egyptian or Gulf dialect) or English.",
			f"Today is {today.isoformat()} ({today.strftime('%A')}).",
			f"The user's default company is {company}." if company else "",
			"",
			PLAN_FORMAT,
			RULES,
			"Period presets: " + ", ".join(PRESETS) + ".",
			"Date groupings: " + ", ".join(DATE_GROUPS) + ".",
			"",
			"METRIC CATALOG:",
			describe_catalog(catalog),
		]
	)
	messages = [{"role": "system", "content": system}]
	if previous and previous.get("plan"):
		prev_question = str(previous.get("question") or "")[:MAX_HISTORY_QUESTION]
		messages.append({"role": "user", "content": prev_question or "(previous question)"})
		messages.append({"role": "assistant", "content": json.dumps(_plan_for_history(previous["plan"]))})
	messages.append({"role": "user", "content": question})
	return messages


def _plan_for_history(spec: dict) -> dict:
	"""The previous spec in the plan format (dates instead of internal keys)."""
	if spec.get("type") != "query":
		return {"type": spec.get("type", "unsupported")}
	period = spec.get("period")
	if period:
		period = {"preset": period["preset"]} if period.get("preset") else {"from": period["from"], "to": period["to"]}
	return {
		"type": "query",
		"metric": spec.get("metric"),
		"group_by": spec.get("group_by"),
		"period": period if spec.get("period_source") == "question" else None,
		"filters": spec.get("filters") or {},
		"top": spec.get("top"),
		"sort": spec.get("sort"),
		"chart": spec.get("chart"),
		"compare_previous": bool(spec.get("compare_previous")),
	}


def build_explain_messages(question: str, metric: dict, facts: dict) -> list[dict]:
	"""Messages asking for a one or two sentence comment on an aggregated result."""
	system = (
		"You comment on a business figure for a manager in one or two short sentences, "
		"in the language of the question. Use only the figures given; do not add, compute or "
		"estimate any other number, and do not repeat the whole table."
	)
	user = json.dumps(
		{"question": question, "metric": metric["label"], "definition": metric["description"], "result": facts},
		ensure_ascii=False,
		default=str,
	)
	return [{"role": "system", "content": system}, {"role": "user", "content": user}]
