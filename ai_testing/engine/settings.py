"""Settings, catalog and request context (frappe)."""

from __future__ import annotations

from datetime import date

import frappe
from frappe.utils import getdate

from ai_testing.assistant.catalog import build_catalog

SETTINGS_DOCTYPE = "AI Assistant Settings"
DEFAULT_REQUESTS_PER_MINUTE = 20
WEEKDAYS = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}


def get_settings() -> frappe._dict:
	"""Settings with the decrypted API key. Never send the result to the browser."""
	doc = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	return frappe._dict(
		enabled=bool(doc.enabled),
		provider=doc.provider,
		base_url=(doc.base_url or "").rstrip("/"),
		api_key=doc.get_password("api_key", raise_exception=False) or "",
		model=doc.model or "",
		json_mode=bool(doc.json_mode),
		explain_results=bool(doc.explain_results),
		reference_date=doc.reference_date,
		requests_per_minute=doc.requests_per_minute or DEFAULT_REQUESTS_PER_MINUTE,
		disabled_metrics=set(doc.get_disabled_metrics()),
	)


def get_extra_metrics() -> list[dict]:
	"""Metrics added by other apps: hook `ai_assistant_metrics` = ["app.module.function"],
	each function returning a list of metric dicts (see assistant/catalog.py)."""
	metrics = []
	for path in frappe.get_hooks("ai_assistant_metrics") or []:
		metrics.extend(frappe.get_attr(path)() or [])
	return metrics


def get_catalog(include_disabled: bool = False) -> dict:
	disabled = None
	if not include_disabled:
		disabled = get_settings().disabled_metrics
	return build_catalog(get_extra_metrics(), disabled=disabled)


def get_today(settings=None) -> date:
	settings = settings or get_settings()
	return getdate(settings.reference_date) if settings.reference_date else getdate()


def get_week_start() -> int:
	try:
		name = frappe.db.get_single_value("System Settings", "first_day_of_the_week")
	except Exception:
		name = None
	return WEEKDAYS.get(name or "Sunday", 6)


def get_default_company() -> str | None:
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
