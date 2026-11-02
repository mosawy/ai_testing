"""Parse and validate the model's plan. Pure module (no frappe import).

The model answers with a small JSON object. Nothing in it is trusted: the metric must
be in the catalog, the grouping and filters must be dimensions of that metric, and the
period is turned into dates here. The result ("spec") is what the executor runs.
"""

from __future__ import annotations

import json
import re
from datetime import date

from ai_testing.assistant.catalog import DATE_GROUPS
from ai_testing.assistant.periods import PeriodError, bucket_starts, max_buckets, resolve_period

PLAN_TYPES = ("query", "unsupported", "chat")
CHART_TYPES = ("auto", "bar", "line", "pie", "donut", "number", "none")
DEFAULT_TOP = 10
MAX_FILTER_VALUES = 20
MAX_FILTER_VALUE_LENGTH = 140
MAX_MESSAGE_LENGTH = 1000

# ASCII, Arabic-Indic and Extended Arabic-Indic digits
_DIGITS = re.compile(r"[0-9٠-٩۰-۹]")


class PlanError(ValueError):
	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code = code
		self.message = message


def has_digits(text: str) -> bool:
	return bool(_DIGITS.search(text or ""))


def parse_reply(text: str) -> dict:
	"""Extract the first JSON object from the model's reply (code fences and prose allowed)."""
	if not text or not text.strip():
		raise PlanError("empty_reply", "The model returned an empty reply.")
	start = text.find("{")
	while start != -1:
		try:
			value, _end = json.JSONDecoder().raw_decode(text[start:])
		except ValueError:
			start = text.find("{", start + 1)
			continue
		if isinstance(value, dict):
			return value
		start = text.find("{", start + 1)
	raise PlanError("invalid_json", "The model's reply is not a JSON object.")


def _clean_values(value) -> list[str]:
	values = value if isinstance(value, list) else [value]
	out = []
	for v in values:
		if v is None or isinstance(v, (dict, list)):
			raise PlanError("bad_filter", "Filter values must be text.")
		text = str(v).strip()
		if not text:
			continue
		if len(text) > MAX_FILTER_VALUE_LENGTH:
			raise PlanError("bad_filter", "A filter value is too long.")
		if text not in out:
			out.append(text)
	if not out:
		raise PlanError("bad_filter", "A filter has no value.")
	if len(out) > MAX_FILTER_VALUES:
		raise PlanError("bad_filter", f"A filter can have at most {MAX_FILTER_VALUES} values.")
	return out


def _iso_period(rng: dict | None) -> dict | None:
	if not rng:
		return None
	return {
		"from": rng["from"].isoformat(),
		"to": rng["to"].isoformat(),
		"preset": rng.get("preset"),
		"label": rng.get("label"),
	}


def validate_plan(raw: dict, catalog: dict, today: date, week_start: int = 0, max_top: int = 50) -> dict:
	"""Return the normalised spec for a plan, or raise PlanError."""
	if not isinstance(raw, dict):
		raise PlanError("invalid_plan", "The plan must be a JSON object.")

	plan_type = str(raw.get("type") or "query").strip().lower()
	if plan_type not in PLAN_TYPES:
		raise PlanError("invalid_plan", f"Unknown plan type {plan_type!r}.")

	if plan_type == "unsupported":
		reason = str(raw.get("reason") or "").strip()[:MAX_MESSAGE_LENGTH]
		return {"type": "unsupported", "reason": reason}

	if plan_type == "chat":
		message = str(raw.get("message") or "").strip()[:MAX_MESSAGE_LENGTH]
		# The model may only greet or explain; any number it writes could be invented
		if not message or has_digits(message):
			return {"type": "unsupported", "reason": "", "notes": ["chat_with_numbers"] if message else []}
		return {"type": "chat", "message": message}

	metric_key = str(raw.get("metric") or "").strip()
	metric = catalog.get(metric_key)
	if not metric:
		raise PlanError("unknown_metric", f"Unknown metric {metric_key!r}.")

	notes: list[str] = []

	group_by = raw.get("group_by") or None
	group_kind = None
	if group_by is not None:
		group_by = str(group_by).strip().lower()
		if group_by in DATE_GROUPS:
			if not metric["date_field"]:
				raise PlanError("bad_group_by", f"{metric['label']} has no date, it cannot be grouped by {group_by}.")
			group_kind = "date"
		elif group_by in metric["dimensions"]:
			group_kind = "dimension"
		else:
			raise PlanError("bad_group_by", f"{metric['label']} cannot be grouped by {group_by!r}.")

	try:
		rng = resolve_period(raw.get("period"), today, week_start)
	except PeriodError as e:
		raise PlanError("bad_period", str(e)) from e
	period_source = "question" if rng else None
	if rng and not metric["date_field"]:
		rng = None
		period_source = None
		notes.append("period_ignored")
	if not rng and metric["default_period"] and raw.get("period") in (None, "", {}):
		rng = resolve_period(metric["default_period"], today, week_start)
		period_source = "default"

	if group_kind == "date" and rng:
		count = len(bucket_starts(rng["from"], rng["to"], group_by, week_start))
		if count > max_buckets(group_by):
			raise PlanError("too_many_buckets", f"Too many {group_by}s in this period ({count}); pick a coarser grouping.")

	filters_raw = raw.get("filters") or {}
	if not isinstance(filters_raw, dict):
		raise PlanError("bad_filter", "Filters must be an object.")
	filters = {}
	for key, value in filters_raw.items():
		key = str(key).strip().lower()
		if key not in metric["dimensions"]:
			raise PlanError("unknown_filter", f"{metric['label']} cannot be filtered by {key!r}.")
		filters[key] = _clean_values(value)

	try:
		top = int(raw.get("top") or DEFAULT_TOP)
	except (TypeError, ValueError):
		top = DEFAULT_TOP
	top = min(max(top, 1), max_top)

	sort = str(raw.get("sort") or "desc").strip().lower()
	if sort not in ("asc", "desc"):
		sort = "desc"

	chart = str(raw.get("chart") or "auto").strip().lower()
	if chart not in CHART_TYPES:
		chart = "auto"

	compare = bool(raw.get("compare_previous"))
	if compare and (not rng or group_kind == "date"):
		compare = False
		notes.append("compare_dropped")

	return {
		"type": "query",
		"metric": metric_key,
		"group_by": group_by,
		"group_kind": group_kind,
		"period": _iso_period(rng),
		"period_source": period_source,
		"filters": filters,
		"top": top,
		"sort": sort,
		"chart": chart,
		"compare_previous": compare,
		"notes": notes,
	}
