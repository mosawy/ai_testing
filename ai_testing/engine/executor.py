"""Run a validated spec with frappe.get_list, as the current user.

frappe.get_list applies role permissions, User Permissions, owner-only rules and
permlevel field rules. Nothing here uses raw SQL or ignore_permissions on documents.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.model import default_fields, get_permitted_fields
from frappe.utils import getdate
from frappe.utils.nestedset import get_descendants_of

from ai_testing.assistant.catalog import LINK_TITLE_FIELDS
from ai_testing.assistant.periods import previous_period

MAX_GROUP_ROWS = 20000
MAX_MATCHES = 8


class ExecutionError(Exception):
	"""A question that cannot be answered as asked (shown to the user, not an error log)."""

	def __init__(self, code: str, message: str, data: dict | None = None):
		super().__init__(message)
		self.code = code
		self.message = message
		self.data = data or {}


def _col(table: str, field: str) -> str:
	return f"`tab{table}`.{field}"


def _check_field(metric: dict, table: str, field: str, label: str):
	if field in default_fields:
		return
	parenttype = metric["doctype"] if table != metric["doctype"] else None
	permitted = get_permitted_fields(doctype=table, parenttype=parenttype)
	if field not in permitted:
		raise ExecutionError("permission", _("You do not have permission to see {0}.").format(_(label)))


def check_permissions(spec: dict, metric: dict):
	if not frappe.has_permission(metric["doctype"], "read"):
		raise ExecutionError(
			"permission", _("You do not have permission to read {0}.").format(_(metric["doctype"]))
		)
	if metric["field"]:
		_check_field(metric, metric["table"], metric["field"], metric["label"])
	if metric["date_field"]:
		_check_field(metric, metric["doctype"], metric["date_field"], metric["date_field"])
	used = set(spec["filters"])
	if spec["group_kind"] == "dimension":
		used.add(spec["group_by"])
	for key in used:
		dim = metric["dimensions"][key]
		_check_field(metric, dim["table"], dim["field"], dim["label"])


# --- filter values -----------------------------------------------------------------


def _title_field(link: str) -> str | None:
	field = LINK_TITLE_FIELDS.get(link) or frappe.get_meta(link).title_field
	if field and field != "name" and frappe.get_meta(link).has_field(field):
		return field
	return None


def _exact_match(link: str, text: str) -> str | None:
	try:
		names = frappe.get_list(link, filters={"name": text}, pluck="name", limit_page_length=1)
	except frappe.PermissionError:
		# The user cannot browse this master; accept a name they typed exactly
		return text if frappe.db.exists(link, text) else None
	return names[0] if names else None


def _search(link: str, text: str) -> list[dict]:
	title = _title_field(link)
	fields = ["name", title] if title else ["name"]
	or_filters = [[link, "name", "like", f"%{text}%"]]
	if title:
		or_filters.append([link, title, "like", f"%{text}%"])
	try:
		rows = frappe.get_list(
			link, fields=fields, or_filters=or_filters, limit_page_length=MAX_MATCHES + 1, order_by="name asc"
		)
	except frappe.PermissionError:
		return []
	return [{"value": r.name, "label": (r.get(title) if title else None) or r.name} for r in rows]


def resolve_value(dim_key: str, dim: dict, text: str) -> str:
	"""Turn the text the user wrote into a record name, or raise clarify / no_match."""
	link = dim["link"]
	if not link:
		return text
	exact = _exact_match(link, text)
	if exact:
		return exact
	matches = _search(link, text)
	if len(matches) == 1:
		return matches[0]["value"]
	lowered = text.strip().lower()
	same = [m for m in matches if m["label"].strip().lower() == lowered or m["value"].lower() == lowered]
	if len(same) == 1:
		return same[0]["value"]
	if not matches:
		raise ExecutionError(
			"no_match",
			_("No {0} matches \"{1}\".").format(_(dim["label"]), text),
			{"dimension": dim_key, "value": text},
		)
	raise ExecutionError(
		"clarify",
		_("Which {0} do you mean by \"{1}\"?").format(_(dim["label"]), text),
		{"dimension": dim_key, "label": dim["label"], "value": text, "options": matches[:MAX_MATCHES]},
	)


def _expand_tree(link: str, names: list[str]) -> tuple[list[str], bool]:
	"""Add sub-groups for tree masters (Territory, Customer Group, Item Group, Warehouse...)."""
	if not frappe.get_meta(link).is_tree:
		return names, False
	out = list(names)
	for name in names:
		try:
			children = get_descendants_of(link, name, ignore_permissions=True)
		except TypeError:
			# lft/rgt not set (tree never rebuilt): filter on the record itself
			children = []
		for child in children:
			if child not in out:
				out.append(child)
	return out, len(out) > len(names)


def resolve_filters(spec: dict, metric: dict) -> tuple[list, list[dict]]:
	"""get_list filters for the spec's dimension filters, plus their description."""
	filters, described = [], []
	for key, texts in spec["filters"].items():
		dim = metric["dimensions"][key]
		names = [resolve_value(key, dim, text) for text in texts]
		with_children = False
		if dim["link"]:
			names, with_children = _expand_tree(dim["link"], names)
		filters.append([dim["table"], dim["field"], "in", names])
		described.append(
			{
				"dimension": key,
				"label": dim["label"],
				"values": names[: len(texts)] if with_children else names,
				"with_children": with_children,
			}
		)
	return filters, described


# --- queries -----------------------------------------------------------------------


def _fixed_filters(metric: dict, today) -> list:
	out = []
	for table, field, op, value in metric["filters"]:
		if value == "{today}":
			value = today.isoformat()
		out.append([table, field, op, value])
	return out


def _period_filters(metric: dict, rng: dict | None) -> list:
	if not rng:
		return []
	start, end = getdate(rng["from"]), getdate(rng["to"])
	field = metric["date_field"]
	return [
		[metric["doctype"], field, ">=", start.isoformat()],
		[metric["doctype"], field, "<", (end + timedelta(days=1)).isoformat()],
	]


def _uses_child(metric: dict, spec: dict) -> bool:
	tables = {metric["table"]}
	keys = set(spec["filters"]) | ({spec["group_by"]} if spec["group_kind"] == "dimension" else set())
	tables |= {metric["dimensions"][k]["table"] for k in keys}
	return tables != {metric["doctype"]}


def _value_expression(metric: dict, spec: dict) -> str:
	if metric["aggregate"] == "sum":
		return f"sum({_col(metric['table'], metric['field'])})"
	if _uses_child(metric, spec):
		return f"count(distinct {_col(metric['doctype'], 'name')})"
	return "count(*)"


def _query(spec: dict, metric: dict, filters: list):
	"""Return a number (no grouping) or a list of (group key, value)."""
	fields = [f"{_value_expression(metric, spec)} as value"]
	group_by = None
	if spec["group_kind"] == "date":
		group_by = _col(metric["doctype"], metric["date_field"])
		if metric["date_is_datetime"]:
			group_by = f"date({group_by})"
	elif spec["group_kind"] == "dimension":
		dim = metric["dimensions"][spec["group_by"]]
		group_by = _col(dim["table"], dim["field"])

	if not group_by:
		rows = frappe.get_list(metric["doctype"], fields=fields, filters=filters, limit_page_length=1)
		return float((rows[0].get("value") if rows else 0) or 0), False

	rows = frappe.get_list(
		metric["doctype"],
		fields=[f"{group_by} as grp", *fields],
		filters=filters,
		group_by=group_by,
		order_by=f"{group_by} asc",
		limit_page_length=MAX_GROUP_ROWS,
	)
	pairs = [(r.get("grp"), float(r.get("value") or 0)) for r in rows]
	return pairs, len(rows) >= MAX_GROUP_ROWS


def _labels(spec: dict, metric: dict, keys) -> dict:
	if spec["group_kind"] != "dimension":
		return {}
	link = metric["dimensions"][spec["group_by"]]["link"]
	keys = [k for k in keys if k not in (None, "")]
	title = _title_field(link) if link else None
	if not title or not keys:
		return {}
	out = {}
	try:
		for chunk_start in range(0, len(keys), 500):
			chunk = keys[chunk_start : chunk_start + 500]
			for r in frappe.get_list(
				link, filters={"name": ["in", chunk]}, fields=["name", title], limit_page_length=len(chunk)
			):
				label = r.get(title)
				if label and label != r.name:
					out[str(r.name)] = label if link != "Item" else f"{r.name}: {label}"
	except frappe.PermissionError:
		return {}
	return out


def _company(spec: dict, metric: dict, default_company: str | None) -> str | None:
	if not metric["company_field"] or "company" in spec["filters"] or spec["group_by"] == "company":
		return None
	return default_company


def run_spec(spec: dict, metric: dict, today, default_company: str | None) -> dict:
	"""Execute a query spec. Returns the raw aggregates and the basis of the answer."""
	check_permissions(spec, metric)
	dim_filters, described = resolve_filters(spec, metric)

	base = _fixed_filters(metric, today) + dim_filters
	company = _company(spec, metric, default_company)
	if company:
		base.append([metric["doctype"], metric["company_field"], "=", company])

	current, truncated = _query(spec, metric, base + _period_filters(metric, spec["period"]))

	previous = None
	prev_rng = None
	if spec["compare_previous"] and spec["period"]:
		prev_rng = previous_period({"from": getdate(spec["period"]["from"]), "to": getdate(spec["period"]["to"])})
		previous, prev_truncated = _query(spec, metric, base + _period_filters(metric, prev_rng))
		truncated = truncated or prev_truncated

	keys = []
	if spec["group_kind"] == "dimension":
		keys = [k for k, _v in current] + [k for k, _v in previous or []]

	return {
		"current": current,
		"previous": previous,
		"labels": _labels(spec, metric, list(dict.fromkeys(keys))),
		"company": company,
		"filters": described,
		"previous_period": (
			{"from": prev_rng["from"].isoformat(), "to": prev_rng["to"].isoformat()} if prev_rng else None
		),
		"truncated_groups": truncated,
	}
