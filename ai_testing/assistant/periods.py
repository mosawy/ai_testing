"""Date periods and date buckets. Pure module (no frappe import).

The model never does date arithmetic for common periods: it returns a preset
("last_month") and the server turns it into dates here. Weeks start on `week_start`
(Python weekday: Monday = 0 ... Sunday = 6), taken from System Settings.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

PRESETS = {
	"today": "Today",
	"yesterday": "Yesterday",
	"this_week": "This Week",
	"last_week": "Last Week",
	"this_month": "This Month",
	"last_month": "Last Month",
	"this_quarter": "This Quarter",
	"last_quarter": "Last Quarter",
	"this_year": "This Year",
	"last_year": "Last Year",
}

MAX_LAST_DAYS = 3660
MAX_SPAN_DAYS = 366 * 20


class PeriodError(ValueError):
	pass


def parse_date(value) -> date:
	if isinstance(value, date):
		return value
	if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
		raise PeriodError(f"Date must be YYYY-MM-DD, got {value!r}")
	try:
		return date.fromisoformat(value.strip())
	except ValueError as e:
		raise PeriodError(f"Invalid date {value!r}") from e


def month_end(d: date) -> date:
	return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, months: int) -> date:
	"""First day of the month `months` away from d's month."""
	index = d.year * 12 + (d.month - 1) + months
	return date(index // 12, index % 12 + 1, 1)


def week_start_of(d: date, week_start: int) -> date:
	return d - timedelta(days=(d.weekday() - week_start) % 7)


def quarter_start(d: date) -> date:
	return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _preset_range(preset: str, today: date, week_start: int) -> tuple[date, date]:
	if preset == "today":
		return today, today
	if preset == "yesterday":
		d = today - timedelta(days=1)
		return d, d
	if preset in ("this_week", "last_week"):
		start = week_start_of(today, week_start)
		if preset == "last_week":
			start -= timedelta(days=7)
		return start, start + timedelta(days=6)
	if preset in ("this_month", "last_month"):
		start = today.replace(day=1)
		if preset == "last_month":
			start = add_months(start, -1)
		return start, month_end(start)
	if preset in ("this_quarter", "last_quarter"):
		start = quarter_start(today)
		if preset == "last_quarter":
			start = add_months(start, -3)
		return start, month_end(add_months(start, 2))
	if preset in ("this_year", "last_year"):
		year = today.year - (preset == "last_year")
		return date(year, 1, 1), date(year, 12, 31)
	raise PeriodError(f"Unknown period {preset!r}")


def resolve_period(period, today: date, week_start: int = 0) -> dict | None:
	"""Turn the model's period into {"from", "to", "preset", "label"} (dates), or None.

	Accepted forms: None, "last_month", {"preset": "last_month"}, {"last_days": 30},
	{"month": "2026-03"}, {"quarter": "2026-Q1"}, {"year": 2025},
	{"from": "2026-01-01", "to": "2026-03-31"}.
	"""
	if period in (None, "", {}):
		return None
	if isinstance(period, str):
		period = {"preset": period}
	if not isinstance(period, dict):
		raise PeriodError("Period must be an object")

	preset = None
	label = None
	if period.get("preset"):
		preset = str(period["preset"]).strip().lower()
		if preset not in PRESETS:
			raise PeriodError(f"Unknown period {preset!r}")
		start, end = _preset_range(preset, today, week_start)
		label = PRESETS[preset]
	elif period.get("last_days") is not None:
		try:
			days = int(period["last_days"])
		except (TypeError, ValueError) as e:
			raise PeriodError("last_days must be a whole number") from e
		if not 1 <= days <= MAX_LAST_DAYS:
			raise PeriodError(f"last_days must be between 1 and {MAX_LAST_DAYS}")
		start, end = today - timedelta(days=days - 1), today
		label = f"Last {days} Days"
	elif period.get("month"):
		m = re.fullmatch(r"(\d{4})-(\d{1,2})", str(period["month"]).strip())
		if not m or not 1 <= int(m.group(2)) <= 12:
			raise PeriodError("month must be YYYY-MM")
		start = date(int(m.group(1)), int(m.group(2)), 1)
		end = month_end(start)
	elif period.get("quarter"):
		m = re.fullmatch(r"(\d{4})-?Q([1-4])", str(period["quarter"]).strip(), flags=re.IGNORECASE)
		if not m:
			raise PeriodError("quarter must be YYYY-Qn")
		start = date(int(m.group(1)), 3 * (int(m.group(2)) - 1) + 1, 1)
		end = month_end(add_months(start, 2))
	elif period.get("year"):
		try:
			year = int(period["year"])
		except (TypeError, ValueError) as e:
			raise PeriodError("year must be a number") from e
		start, end = date(year, 1, 1), date(year, 12, 31)
	elif period.get("from") or period.get("to"):
		if not period.get("from") or not period.get("to"):
			raise PeriodError("A custom period needs both from and to")
		start, end = parse_date(period["from"]), parse_date(period["to"])
	else:
		raise PeriodError("Unrecognised period")

	if not 1900 <= start.year <= 2200 or not 1900 <= end.year <= 2200:
		raise PeriodError("Period year out of range")
	if start > end:
		raise PeriodError("Period starts after it ends")
	if (end - start).days > MAX_SPAN_DAYS:
		raise PeriodError("Period is longer than 20 years")
	return {"from": start, "to": end, "preset": preset, "label": label}


def previous_period(rng: dict) -> dict:
	"""The period of the same length just before `rng` (whole months shift by months)."""
	start, end = rng["from"], rng["to"]
	if start.day == 1 and end == month_end(end):
		months = (end.year * 12 + end.month) - (start.year * 12 + start.month) + 1
		prev_start = add_months(start, -months)
		prev_end = month_end(add_months(start, -1))
	else:
		length = (end - start).days + 1
		prev_end = start - timedelta(days=1)
		prev_start = prev_end - timedelta(days=length - 1)
	return {"from": prev_start, "to": prev_end, "preset": None, "label": None}


def bucket_start(d: date, unit: str, week_start: int = 0) -> date:
	if unit == "day":
		return d
	if unit == "week":
		return week_start_of(d, week_start)
	if unit == "month":
		return d.replace(day=1)
	if unit == "quarter":
		return quarter_start(d)
	if unit == "year":
		return date(d.year, 1, 1)
	raise PeriodError(f"Unknown date grouping {unit!r}")


def next_bucket(start: date, unit: str) -> date:
	if unit == "day":
		return start + timedelta(days=1)
	if unit == "week":
		return start + timedelta(days=7)
	if unit == "month":
		return add_months(start, 1)
	if unit == "quarter":
		return add_months(start, 3)
	if unit == "year":
		return date(start.year + 1, 1, 1)
	raise PeriodError(f"Unknown date grouping {unit!r}")


def bucket_starts(start: date, end: date, unit: str, week_start: int = 0) -> list[date]:
	"""Every bucket start from the bucket holding `start` to the one holding `end`."""
	out = []
	current = bucket_start(start, unit, week_start)
	while current <= end:
		out.append(current)
		current = next_bucket(current, unit)
	return out


def bucket_label(start: date, unit: str) -> str:
	if unit in ("day", "week"):
		return start.isoformat()
	if unit == "month":
		return f"{start.year}-{start.month:02d}"
	if unit == "quarter":
		return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
	if unit == "year":
		return str(start.year)
	raise PeriodError(f"Unknown date grouping {unit!r}")


def max_buckets(unit: str) -> int:
	"""Most buckets a chart may show for a date grouping."""
	return {"day": 400, "week": 260, "month": 240, "quarter": 80, "year": 50}[unit]
