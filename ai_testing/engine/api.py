"""Endpoints of the Ask ERP page."""

from __future__ import annotations

import json
import time

import frappe
from frappe import _

from ai_testing.assistant.catalog import DATE_GROUP_LABELS
from ai_testing.assistant.evaluate import compare_plan, load_questions, summarize
from ai_testing.assistant.plan import PlanError, parse_reply, validate_plan
from ai_testing.assistant.prompt import build_explain_messages, build_planner_messages
from ai_testing.assistant.results import build_answer
from ai_testing.engine import llm
from ai_testing.engine.executor import ExecutionError, run_spec
from ai_testing.engine.settings import (
	get_catalog,
	get_default_company,
	get_settings,
	get_today,
	get_week_start,
)

PAGE_NAME = "ask-erp"
MAX_QUESTION_LENGTH = 500
EXPLAIN_MAX_ROWS = 20

LOG_STATUS = {
	"answered": "Answered",
	"unsupported": "Unsupported",
	"chat": "Chat",
	"clarify": "Clarify",
	"no_match": "No Match",
	"invalid_plan": "Invalid Plan",
	"permission": "Permission",
	"llm_error": "LLM Error",
	"error": "Error",
}


def _check_access(settings):
	if not frappe.get_cached_doc("Page", PAGE_NAME).is_permitted():
		frappe.throw(_("You are not allowed to use the assistant."), frappe.PermissionError)
	if not settings.enabled:
		frappe.throw(_("The assistant is disabled in AI Assistant Settings."))


def _rate_limit(settings):
	key = frappe.cache.make_key(f"ai_testing:ask:{frappe.session.user}")
	count = frappe.cache.incr(key)
	if count == 1:
		frappe.cache.expire(key, 60)
	if count > settings.requests_per_minute:
		frappe.throw(_("Too many questions. Please wait a minute."), frappe.RateLimitExceededError)


def _loads(value):
	if not value:
		return None
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value)
	except ValueError:
		frappe.throw(_("Invalid JSON."))


def _currency(metric: dict, spec: dict, company: str | None) -> str | None:
	if metric["unit"] != "currency" or spec["group_by"] == "company":
		return None
	companies = spec["filters"].get("company") or []
	if not company and len(companies) == 1:
		company = companies[0]
	if company:
		return frappe.get_cached_value("Company", company, "default_currency")
	return frappe.db.get_default("currency")


def _basis(spec: dict, metric: dict, result: dict, answer: dict) -> dict:
	notes = list(spec.get("notes") or [])
	if result["truncated_groups"]:
		notes.append("truncated_groups")
	if answer.get("truncated_buckets"):
		notes.append("truncated_buckets")
	if (
		metric["company_field"]
		and not result["company"]
		and "company" not in spec["filters"]
		and spec["group_by"] != "company"
	):
		notes.append("all_companies")
	if metric["field"] == "outstanding_amount":
		notes.append("party_currency")
	return {
		"source": metric["doctype"],
		"definition": metric["description"],
		"period": spec["period"],
		"period_source": spec["period_source"],
		"previous_period": result["previous_period"],
		"company": result["company"],
		"filters": result["filters"],
		"notes": notes,
	}


def _group(spec: dict, metric: dict) -> dict | None:
	if spec["group_kind"] == "date":
		return {"key": spec["group_by"], "label": DATE_GROUP_LABELS[spec["group_by"]], "kind": "date"}
	if spec["group_kind"] == "dimension":
		dim = metric["dimensions"][spec["group_by"]]
		return {"key": spec["group_by"], "label": dim["label"], "kind": "dimension", "link": dim["link"]}
	return None


def _explain(settings, question: str, metric: dict, spec: dict, answer: dict) -> str | None:
	facts = {
		"period": spec["period"] and {"from": spec["period"]["from"], "to": spec["period"]["to"]},
		"total": answer.get("total"),
		"previous_total": answer.get("previous_total"),
		"change_pct": answer.get("change_pct"),
		"rows": [
			{"group": r["label"] or r["key"], "value": r["value"], **({"previous": r["previous"]} if "previous" in r else {})}
			for r in answer["rows"][:EXPLAIN_MAX_ROWS]
		],
	}
	if answer.get("others"):
		facts["others"] = {"groups": answer["others"]["count"], "value": answer["others"]["value"]}
	try:
		return llm.chat(settings, build_explain_messages(question, metric, facts), max_tokens=250, json_mode=False).strip()
	except llm.LLMError:
		return None


def _log(question, response: dict, spec, started: float, model: str, error: str | None):
	try:
		frappe.get_doc(
			{
				"doctype": "AI Question Log",
				"user": frappe.session.user,
				"question": (question or "")[:2000],
				"status": LOG_STATUS.get(response.get("status"), "Error"),
				"metric": (spec or {}).get("metric"),
				"plan": json.dumps(spec, ensure_ascii=False, indent=1, default=str) if spec else None,
				"error": (error or response.get("message") or "")[:2000] or None,
				"duration_ms": int((time.monotonic() - started) * 1000),
				"result_rows": len(((response.get("answer") or {}).get("rows")) or []),
				"model": model,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="AI Question Log insert failed")


@frappe.whitelist(methods=["POST"])
def ask(question: str | None = None, plan: str | None = None, previous: str | None = None) -> dict:
	"""Answer a question.

	question: what the user typed.
	plan:     optional plan JSON to run without calling the model (clarify choices, chart changes).
	previous: optional {"question", "plan"} of the last answer, for follow-up questions.
	"""
	started = time.monotonic()
	settings = get_settings()
	_check_access(settings)
	_rate_limit(settings)

	question = (question or "").strip()
	if not question and not plan:
		frappe.throw(_("Please type a question."))
	if len(question) > MAX_QUESTION_LENGTH:
		frappe.throw(_("Please keep the question under {0} characters.").format(MAX_QUESTION_LENGTH))

	catalog = get_catalog()
	today = get_today(settings)
	week_start = get_week_start()
	company = get_default_company()
	spec = None
	error = None

	try:
		if plan:
			raw = _loads(plan)
		else:
			prev = _loads(previous)
			messages = build_planner_messages(
				question, catalog, today, prev if isinstance(prev, dict) else None, company
			)
			raw = parse_reply(llm.chat(settings, messages))
		spec = validate_plan(raw, catalog, today, week_start)

		if spec["type"] == "unsupported":
			response = {"status": "unsupported", "message": spec.get("reason") or None}
		elif spec["type"] == "chat":
			response = {"status": "chat", "message": spec["message"]}
		else:
			metric = catalog[spec["metric"]]
			result = run_spec(spec, metric, today, company)
			answer = build_answer(spec, metric, result["current"], result["previous"], result["labels"], week_start)
			response = {
				"status": "answered",
				"metric": {
					"key": metric["key"],
					"label": metric["label"],
					"unit": metric["unit"],
					"doctype": metric["doctype"],
				},
				"group": _group(spec, metric),
				"answer": answer,
				"currency": _currency(metric, spec, result["company"]),
				"basis": _basis(spec, metric, result, answer),
				"explanation": _explain(settings, question, metric, spec, answer)
				if settings.explain_results
				else None,
			}
	except PlanError as e:
		error = f"{e.code}: {e.message}"
		response = {"status": "invalid_plan", "message": _("I could not turn this into a query. Try rephrasing it.")}
	except ExecutionError as e:
		response = {"status": e.code, "message": e.message, **e.data}
	except llm.LLMError as e:
		error = e.message
		response = {"status": "llm_error", "message": e.message}
	except frappe.PermissionError:
		response = {"status": "permission", "message": _("You do not have permission for this data.")}
	except Exception:
		error = frappe.get_traceback()
		frappe.log_error(title="Ask ERP failed")
		response = {"status": "error", "message": _("Something went wrong. The error was logged.")}

	response["question"] = question
	response["plan"] = spec
	_log(question, response, spec, started, settings.model, error)
	return response


@frappe.whitelist()
def get_metrics(include_disabled: int | str = 0) -> list[dict]:
	"""Metric list for the page (example questions) and the settings form."""
	include_disabled = frappe.utils.cint(include_disabled)
	settings = get_settings()
	if include_disabled:
		frappe.only_for("System Manager")
	else:
		_check_access(settings)
	catalog = get_catalog(include_disabled=True)
	return [
		{
			"key": m["key"],
			"label": m["label"],
			"description": m["description"],
			"doctype": m["doctype"],
			"examples": m["examples"],
			"disabled": m["key"] in settings.disabled_metrics,
		}
		for m in catalog.values()
		if include_disabled or m["key"] not in settings.disabled_metrics
	]


@frappe.whitelist()
def test_connection() -> dict:
	"""Send one short message to the configured model (System Manager)."""
	frappe.only_for("System Manager")
	settings = get_settings()
	started = time.monotonic()
	try:
		reply = llm.chat(settings, [{"role": "user", "content": "Reply with the word OK."}], max_tokens=20, json_mode=False)
	except llm.LLMError as e:
		return {"ok": False, "error": e.message, "model": settings.model}
	return {
		"ok": True,
		"model": settings.model,
		"latency_ms": int((time.monotonic() - started) * 1000),
		"reply": reply[:200],
	}


def run_evaluation(limit: int | None = None, verbose: int = 1) -> dict:
	"""Ask the model to plan every question in assistant/eval_questions.json and score the plans.

	Only plans are checked, no data is read. Run with:
	bench --site <site> execute ai_testing.engine.api.run_evaluation
	"""
	frappe.only_for("System Manager")
	settings = get_settings()
	catalog = get_catalog(include_disabled=True)
	today = get_today(settings)
	week_start = get_week_start()
	questions = load_questions()[: int(limit)] if limit else load_questions()

	results = []
	for item in questions:
		row = {"question": item["question"], "problems": [], "error": None, "plan": None}
		try:
			raw = parse_reply(llm.chat(settings, build_planner_messages(item["question"], catalog, today)))
			spec = validate_plan(raw, catalog, today, week_start)
			row["plan"] = spec
			row["problems"] = compare_plan(item["expected"], spec)
		except (PlanError, llm.LLMError) as e:
			row["error"] = getattr(e, "message", str(e))
		results.append(row)
		if verbose:
			status = "PASS" if not row["problems"] and not row["error"] else "FAIL"
			print(f"{status}  {item['question']}")
			for problem in row["problems"]:
				print(f"      {problem}")
			if row["error"]:
				print(f"      error: {row['error']}")

	summary = summarize(results)
	summary["model"] = settings.model
	if verbose:
		print(f"\n{summary['passed']}/{summary['total']} passed ({summary['score_pct']} %) with {settings.model}")
	for failure in summary["failures"]:
		failure.pop("plan", None)
	return summary
