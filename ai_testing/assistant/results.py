"""Turn query rows into the answer the page shows. Pure module (no frappe import).

The executor returns raw aggregates; this module applies the metric sign, fills empty
date buckets, keeps the top N groups plus "Others", compares with the previous period
and picks the chart.
"""

from __future__ import annotations

from datetime import date

from ai_testing.assistant.periods import bucket_label, bucket_start, bucket_starts, max_buckets, parse_date

NONE_KEY = "__none__"
OTHERS_KEY = "__others__"
PIE_MAX_SLICES = 6
PIE_REQUESTED_MAX_SLICES = 12


def round_value(value, unit: str):
	value = float(value or 0)
	if unit == "number":
		return int(round(value))
	return round(value, 3 if unit == "qty" else 2)


def change_pct(current, previous):
	if previous in (None, 0) or current is None:
		return None
	return round((current - previous) / abs(previous) * 100, 1)


def _row(key, label, value, previous, unit, compare):
	row = {"key": key, "label": label, "value": round_value(value, unit)}
	if compare:
		row["previous"] = round_value(previous, unit)
		row["change_pct"] = change_pct(row["value"], row["previous"])
	return row


def _signed(pairs, sign):
	return [(k, float(v or 0) * sign) for k, v in pairs or []]


def _date_rows(spec, current, week_start):
	unit = spec["group_by"]
	totals: dict[date, float] = {}
	for raw_date, value in current:
		if raw_date in (None, ""):
			continue
		d = parse_date(raw_date if isinstance(raw_date, str) else raw_date.isoformat()[:10])
		start = bucket_start(d, unit, week_start)
		totals[start] = totals.get(start, 0.0) + value

	if spec.get("period"):
		first, last = parse_date(spec["period"]["from"]), parse_date(spec["period"]["to"])
	elif totals:
		first, last = min(totals), max(totals)
	else:
		return [], False

	starts = bucket_starts(first, last, unit, week_start)
	truncated = len(starts) > max_buckets(unit)
	if truncated:
		starts = starts[-max_buckets(unit) :]
	rows = [(bucket_label(s, unit), totals.get(s, 0.0)) for s in starts]
	return rows, truncated


def _dimension_rows(spec, current, previous, labels):
	keys = {}
	for key, value in current:
		keys.setdefault(key, [0.0, 0.0])[0] += value
	for key, value in previous or []:
		keys.setdefault(key, [0.0, 0.0])[1] += value

	ordered = sorted(
		keys.items(),
		key=lambda kv: (kv[1][0], kv[1][1]),
		reverse=spec["sort"] == "desc",
	)
	# Groups with nothing in either period are noise (e.g. a credit note that zeroes a customer)
	ordered = [kv for kv in ordered if kv[1][0] or kv[1][1]]
	top = ordered[: spec["top"]]
	rest = ordered[spec["top"] :]

	rows = []
	for key, (value, prev) in top:
		row_key = NONE_KEY if key in (None, "") else str(key)
		label = (labels or {}).get(row_key) or (None if row_key == NONE_KEY else str(key))
		rows.append((row_key, label, value, prev))
	others = None
	if rest:
		others = (
			len(rest),
			sum(v for _k, (v, _p) in rest),
			sum(p for _k, (_v, p) in rest),
		)
	return rows, others


def choose_chart(spec: dict, kind: str, rows: list[dict], compare: bool) -> str | None:
	"""Chart type for the answer, or None when a number card or table says it better."""
	requested = spec.get("chart") or "auto"
	if requested == "none" or kind == "total" or not rows:
		return None
	if requested == "number":
		return None
	if kind == "date":
		if requested in ("bar", "line"):
			return requested
		return "line" if len(rows) >= 3 else "bar"

	if len(rows) < 2 and requested == "auto":
		return None
	negative = any(r["value"] < 0 for r in rows)
	if compare:
		return "bar"
	if requested in ("pie", "donut"):
		return requested if not negative and len(rows) <= PIE_REQUESTED_MAX_SLICES else "bar"
	if requested in ("bar", "line"):
		return "bar"
	return "donut" if not negative and len(rows) <= PIE_MAX_SLICES else "bar"


def build_answer(
	spec: dict,
	metric: dict,
	current,
	previous=None,
	labels: dict | None = None,
	week_start: int = 0,
) -> dict:
	"""Build the answer.

	`current` / `previous` are a number for an ungrouped spec, else a list of
	(group key, value) pairs; for a date grouping the key is a date or ISO date string.
	`labels` maps group keys to display titles (link titles such as customer names).
	"""
	unit = metric["unit"]
	sign = metric.get("sign", 1)
	compare = bool(spec.get("compare_previous"))
	kind = spec.get("group_kind") or "total"
	answer = {"kind": kind, "rows": [], "others": None, "chart": None, "truncated_buckets": False}

	if kind == "total":
		value = float(current or 0) * sign
		answer["total"] = round_value(value, unit)
		if compare:
			prev = float(previous or 0) * sign
			answer["previous_total"] = round_value(prev, unit)
			answer["change_pct"] = change_pct(answer["total"], answer["previous_total"])
		return answer

	current = _signed(current, sign)
	if kind == "date":
		pairs, truncated = _date_rows(spec, current, week_start)
		answer["truncated_buckets"] = truncated
		answer["rows"] = [_row(label, label, value, None, unit, False) for label, value in pairs]
		answer["total"] = round_value(sum(v for _l, v in pairs), unit)
	else:
		previous = _signed(previous, sign) if compare else None
		rows, others = _dimension_rows(spec, current, previous, labels)
		answer["rows"] = [_row(k, label, v, p, unit, compare) for k, label, v, p in rows]
		total = sum(v for _k, v in current)
		answer["total"] = round_value(total, unit)
		if compare:
			prev_total = sum(v for _k, v in previous)
			answer["previous_total"] = round_value(prev_total, unit)
			answer["change_pct"] = change_pct(answer["total"], answer["previous_total"])
		if others:
			count, value, prev = others
			answer["others"] = {"count": count, **_row(OTHERS_KEY, None, value, prev, unit, compare)}

	chart_rows = list(answer["rows"])
	if answer["others"]:
		chart_rows.append(answer["others"])
	chart_type = choose_chart(spec, kind, chart_rows, compare)
	if chart_type:
		datasets = [{"key": "current", "values": [r["value"] for r in chart_rows]}]
		if compare:
			datasets.append({"key": "previous", "values": [r["previous"] for r in chart_rows]})
		answer["chart"] = {
			"type": chart_type,
			"labels": [r["key"] if r["label"] is None else r["label"] for r in chart_rows],
			"datasets": datasets,
		}
	return answer
