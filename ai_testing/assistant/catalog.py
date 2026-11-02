"""Metric catalog: the only measures the assistant can answer.

Pure module (no frappe import). Every number the user sees is computed by the server
from one of these definitions; the language model only picks a metric key, a grouping,
a period and filter values.

Fields were checked against the ERPNext version-15 DocType JSON files.

A metric:
	key            unique id the model returns
	label          short name shown to the user
	description    what is counted, shown to the model and in the "how calculated" line
	doctype        DocType queried with frappe.get_list (permissions are checked on it)
	table          DocType holding the value field (the doctype or one of its child tables)
	aggregate      "sum" or "count"
	field          value field on `table` (None for count)
	sign           1, or -1 to show negative totals as positive (returns)
	date_field     date field on `doctype` used for periods and date grouping (None: no period)
	date_is_datetime  True when date_field is a Datetime column (creation)
	default_period preset used when the question names no period (None: all dates)
	filters        fixed filters [table, field, operator, value]; value "{today}" is today's date
	company_field  company field on `doctype`, filtered to the user's default company
	dimensions     {dimension key: {"table", "field", "link"}} allowed for grouping and filtering
	unit           "currency", "number" or "qty"
	examples       sample questions (English or Arabic), shown to the model
"""

from __future__ import annotations

import copy

AGGREGATES = ("sum", "count")
UNITS = ("currency", "number", "qty")
DATE_GROUPS = ("day", "week", "month", "quarter", "year")
OPERATORS = ("=", "!=", ">", "<", ">=", "<=", "in", "not in")

# Field searched, besides the name, when the user types a value for a link dimension
LINK_TITLE_FIELDS = {
	"Customer": "customer_name",
	"Supplier": "supplier_name",
	"Item": "item_name",
}

DIMENSION_LABELS = {
	"customer": "Customer",
	"customer_group": "Customer Group",
	"territory": "Territory",
	"company": "Company",
	"cost_center": "Cost Center",
	"project": "Project",
	"sales_partner": "Sales Partner",
	"item": "Item",
	"item_group": "Item Group",
	"brand": "Brand",
	"warehouse": "Warehouse",
	"supplier": "Supplier",
	"supplier_group": "Supplier Group",
	"mode_of_payment": "Mode of Payment",
}

DATE_GROUP_LABELS = {
	"day": "Day",
	"week": "Week",
	"month": "Month",
	"quarter": "Quarter",
	"year": "Year",
}


def _dim(table: str, field: str, link: str | None = None) -> dict:
	return {"table": table, "field": field, "link": link}


def _sales_invoice_dims() -> dict:
	si = "Sales Invoice"
	return {
		"customer": _dim(si, "customer", "Customer"),
		"customer_group": _dim(si, "customer_group", "Customer Group"),
		"territory": _dim(si, "territory", "Territory"),
		"company": _dim(si, "company", "Company"),
		"cost_center": _dim(si, "cost_center", "Cost Center"),
		"project": _dim(si, "project", "Project"),
		"sales_partner": _dim(si, "sales_partner", "Sales Partner"),
	}


def _sales_item_dims() -> dict:
	sii = "Sales Invoice Item"
	si = "Sales Invoice"
	return {
		"item": _dim(sii, "item_code", "Item"),
		"item_group": _dim(sii, "item_group", "Item Group"),
		"brand": _dim(sii, "brand", "Brand"),
		"warehouse": _dim(sii, "warehouse", "Warehouse"),
		"customer": _dim(si, "customer", "Customer"),
		"customer_group": _dim(si, "customer_group", "Customer Group"),
		"territory": _dim(si, "territory", "Territory"),
		"company": _dim(si, "company", "Company"),
	}


def _so_dims() -> dict:
	so = "Sales Order"
	return {
		"customer": _dim(so, "customer", "Customer"),
		"customer_group": _dim(so, "customer_group", "Customer Group"),
		"territory": _dim(so, "territory", "Territory"),
		"company": _dim(so, "company", "Company"),
	}


def _supplier_dims(doctype: str) -> dict:
	return {
		"supplier": _dim(doctype, "supplier", "Supplier"),
		"company": _dim(doctype, "company", "Company"),
	}


def _payment_dims(party_link: str) -> dict:
	pe = "Payment Entry"
	party_key = "customer" if party_link == "Customer" else "supplier"
	return {
		party_key: _dim(pe, "party", party_link),
		"mode_of_payment": _dim(pe, "mode_of_payment", "Mode of Payment"),
		"company": _dim(pe, "company", "Company"),
	}


def _bin_dims() -> dict:
	return {
		"item": _dim("Bin", "item_code", "Item"),
		"warehouse": _dim("Bin", "warehouse", "Warehouse"),
	}


def _submitted(doctype: str) -> list:
	return [doctype, "docstatus", "=", 1]


BUILTIN_METRICS: list[dict] = [
	# --- Selling -------------------------------------------------------------------
	{
		"key": "net_sales",
		"label": "Net Sales",
		"description": (
			"Net total of submitted Sales Invoices in company currency, before taxes. "
			"Credit notes (returns) are negative, so they are deducted."
		),
		"doctype": "Sales Invoice",
		"aggregate": "sum",
		"field": "base_net_total",
		"date_field": "posting_date",
		"default_period": "this_month",
		"filters": [_submitted("Sales Invoice")],
		"company_field": "company",
		"dimensions": _sales_invoice_dims(),
		"unit": "currency",
		"examples": ["sales this month", "مبيعات الشهر اللي فات حسب العميل", "sales by month this year"],
	},
	{
		"key": "sales_invoice_count",
		"label": "Sales Invoices",
		"description": "Number of submitted Sales Invoices, returns excluded.",
		"doctype": "Sales Invoice",
		"aggregate": "count",
		"field": None,
		"date_field": "posting_date",
		"default_period": "this_month",
		"filters": [_submitted("Sales Invoice"), ["Sales Invoice", "is_return", "=", 0]],
		"company_field": "company",
		"dimensions": _sales_invoice_dims(),
		"unit": "number",
		"examples": ["how many invoices this week", "عدد الفواتير النهارده"],
	},
	{
		"key": "sales_returns",
		"label": "Sales Returns",
		"description": "Net total of submitted credit notes (Sales Invoices marked Is Return), shown as a positive amount.",
		"doctype": "Sales Invoice",
		"aggregate": "sum",
		"field": "base_net_total",
		"sign": -1,
		"date_field": "posting_date",
		"default_period": "this_month",
		"filters": [_submitted("Sales Invoice"), ["Sales Invoice", "is_return", "=", 1]],
		"company_field": "company",
		"dimensions": _sales_invoice_dims(),
		"unit": "currency",
		"examples": ["returns last month", "المرتجعات السنة دي حسب العميل"],
	},
	{
		"key": "item_sales_amount",
		"label": "Item Sales Amount",
		"description": (
			"Net amount of items on submitted Sales Invoices in company currency, before taxes; "
			"returned items are deducted. Use for questions about items, item groups or brands."
		),
		"doctype": "Sales Invoice",
		"table": "Sales Invoice Item",
		"aggregate": "sum",
		"field": "base_net_amount",
		"date_field": "posting_date",
		"default_period": "this_month",
		"filters": [_submitted("Sales Invoice")],
		"company_field": "company",
		"dimensions": _sales_item_dims(),
		"unit": "currency",
		"examples": ["top 10 items by sales this year", "مبيعات البراندات الشهر ده"],
	},
	{
		"key": "item_sales_qty",
		"label": "Item Quantity Sold",
		"description": (
			"Quantity sold on submitted Sales Invoices in the item's stock unit; returned "
			"quantities are deducted. Only meaningful per item."
		),
		"doctype": "Sales Invoice",
		"table": "Sales Invoice Item",
		"aggregate": "sum",
		"field": "stock_qty",
		"date_field": "posting_date",
		"default_period": "this_month",
		"filters": [_submitted("Sales Invoice")],
		"company_field": "company",
		"dimensions": _sales_item_dims(),
		"unit": "qty",
		"examples": ["quantity sold per item last month", "اكتر الاصناف مبيعا بالكمية"],
	},
	{
		"key": "receivables",
		"label": "Receivables",
		"description": (
			"Outstanding amount still unpaid on submitted Sales Invoices (customer balances), "
			"in the party account currency. With a period, only invoices posted in that period count."
		),
		"doctype": "Sales Invoice",
		"aggregate": "sum",
		"field": "outstanding_amount",
		"date_field": "posting_date",
		"default_period": None,
		"filters": [_submitted("Sales Invoice"), ["Sales Invoice", "outstanding_amount", "!=", 0]],
		"company_field": "company",
		"dimensions": _sales_invoice_dims(),
		"unit": "currency",
		"examples": ["how much do customers owe us", "مديونية العملاء", "top customers by balance"],
	},
	{
		"key": "overdue_receivables",
		"label": "Overdue Receivables",
		"description": "Outstanding amount on submitted Sales Invoices whose due date has passed.",
		"doctype": "Sales Invoice",
		"aggregate": "sum",
		"field": "outstanding_amount",
		"date_field": "posting_date",
		"default_period": None,
		"filters": [
			_submitted("Sales Invoice"),
			["Sales Invoice", "outstanding_amount", ">", 0],
			["Sales Invoice", "due_date", "<", "{today}"],
		],
		"company_field": "company",
		"dimensions": _sales_invoice_dims(),
		"unit": "currency",
		"examples": ["overdue invoices by customer", "الفلوس المتأخرة على العملاء"],
	},
	{
		"key": "sales_order_value",
		"label": "Sales Orders Booked",
		"description": "Net total of submitted Sales Orders in company currency, by order date.",
		"doctype": "Sales Order",
		"aggregate": "sum",
		"field": "base_net_total",
		"date_field": "transaction_date",
		"default_period": "this_month",
		"filters": [_submitted("Sales Order")],
		"company_field": "company",
		"dimensions": _so_dims(),
		"unit": "currency",
		"examples": ["orders booked this month", "قيمة أوامر البيع السنة دي"],
	},
	{
		"key": "open_sales_orders",
		"label": "Open Sales Orders",
		"description": (
			"Number of submitted Sales Orders not fully delivered (status To Deliver and Bill or To Deliver)."
		),
		"doctype": "Sales Order",
		"aggregate": "count",
		"field": None,
		"date_field": "transaction_date",
		"default_period": None,
		"filters": [
			_submitted("Sales Order"),
			["Sales Order", "status", "in", ["To Deliver and Bill", "To Deliver"]],
		],
		"company_field": "company",
		"dimensions": _so_dims(),
		"unit": "number",
		"examples": ["how many orders are not delivered yet", "أوامر البيع المفتوحة"],
	},
	{
		"key": "open_sales_orders_value",
		"label": "Open Sales Orders Value",
		"description": (
			"Net total of submitted Sales Orders not fully delivered (status To Deliver and Bill "
			"or To Deliver), full order value including any part already delivered."
		),
		"doctype": "Sales Order",
		"aggregate": "sum",
		"field": "base_net_total",
		"date_field": "transaction_date",
		"default_period": None,
		"filters": [
			_submitted("Sales Order"),
			["Sales Order", "status", "in", ["To Deliver and Bill", "To Deliver"]],
		],
		"company_field": "company",
		"dimensions": _so_dims(),
		"unit": "currency",
		"examples": ["value of open orders per customer"],
	},
	{
		"key": "new_customers",
		"label": "New Customers",
		"description": "Number of Customer records created.",
		"doctype": "Customer",
		"aggregate": "count",
		"field": None,
		"date_field": "creation",
		"date_is_datetime": True,
		"default_period": "this_month",
		"filters": [],
		"company_field": None,
		"dimensions": {
			"customer_group": _dim("Customer", "customer_group", "Customer Group"),
			"territory": _dim("Customer", "territory", "Territory"),
		},
		"unit": "number",
		"examples": ["new customers this year by month", "كام عميل جديد الشهر ده"],
	},
	# --- Buying --------------------------------------------------------------------
	{
		"key": "net_purchases",
		"label": "Net Purchases",
		"description": (
			"Net total of submitted Purchase Invoices in company currency, before taxes; "
			"debit notes (returns) are deducted."
		),
		"doctype": "Purchase Invoice",
		"aggregate": "sum",
		"field": "base_net_total",
		"date_field": "posting_date",
		"default_period": "this_month",
		"filters": [_submitted("Purchase Invoice")],
		"company_field": "company",
		"dimensions": _supplier_dims("Purchase Invoice"),
		"unit": "currency",
		"examples": ["purchases this year by supplier", "المشتريات الشهر اللي فات"],
	},
	{
		"key": "payables",
		"label": "Payables",
		"description": (
			"Outstanding amount still unpaid on submitted Purchase Invoices (supplier balances), "
			"in the party account currency."
		),
		"doctype": "Purchase Invoice",
		"aggregate": "sum",
		"field": "outstanding_amount",
		"date_field": "posting_date",
		"default_period": None,
		"filters": [_submitted("Purchase Invoice"), ["Purchase Invoice", "outstanding_amount", "!=", 0]],
		"company_field": "company",
		"dimensions": _supplier_dims("Purchase Invoice"),
		"unit": "currency",
		"examples": ["how much do we owe suppliers", "مستحقات الموردين"],
	},
	{
		"key": "purchase_order_value",
		"label": "Purchase Orders Placed",
		"description": "Net total of submitted Purchase Orders in company currency, by order date.",
		"doctype": "Purchase Order",
		"aggregate": "sum",
		"field": "base_net_total",
		"date_field": "transaction_date",
		"default_period": "this_month",
		"filters": [_submitted("Purchase Order")],
		"company_field": "company",
		"dimensions": _supplier_dims("Purchase Order"),
		"unit": "currency",
		"examples": ["purchase orders this quarter by supplier"],
	},
	# --- Payments ------------------------------------------------------------------
	{
		"key": "payments_received",
		"label": "Payments Received",
		"description": "Submitted Payment Entries of type Receive, paid amount in company currency.",
		"doctype": "Payment Entry",
		"aggregate": "sum",
		"field": "base_paid_amount",
		"date_field": "posting_date",
		"default_period": "this_month",
		"filters": [
			_submitted("Payment Entry"),
			["Payment Entry", "payment_type", "=", "Receive"],
			["Payment Entry", "party_type", "=", "Customer"],
		],
		"company_field": "company",
		"dimensions": _payment_dims("Customer"),
		"unit": "currency",
		"examples": ["collections this month", "التحصيلات الأسبوع ده حسب طريقة الدفع"],
	},
	{
		"key": "payments_made",
		"label": "Payments Made",
		"description": "Submitted Payment Entries of type Pay to suppliers, paid amount in company currency.",
		"doctype": "Payment Entry",
		"aggregate": "sum",
		"field": "base_paid_amount",
		"date_field": "posting_date",
		"default_period": "this_month",
		"filters": [
			_submitted("Payment Entry"),
			["Payment Entry", "payment_type", "=", "Pay"],
			["Payment Entry", "party_type", "=", "Supplier"],
		],
		"company_field": "company",
		"dimensions": _payment_dims("Supplier"),
		"unit": "currency",
		"examples": ["payments to suppliers last month"],
	},
	# --- Stock ---------------------------------------------------------------------
	{
		"key": "stock_qty",
		"label": "Stock Quantity",
		"description": "Current actual quantity in stock (Bin) in the item's stock unit. No period.",
		"doctype": "Bin",
		"aggregate": "sum",
		"field": "actual_qty",
		"date_field": None,
		"default_period": None,
		"filters": [["Bin", "actual_qty", "!=", 0]],
		"company_field": None,
		"dimensions": _bin_dims(),
		"unit": "qty",
		"examples": ["stock of item X per warehouse", "رصيد الصنف ده في المخازن"],
	},
	{
		"key": "stock_value",
		"label": "Stock Value",
		"description": "Current stock value (Bin) at valuation rate in company currency. No period.",
		"doctype": "Bin",
		"aggregate": "sum",
		"field": "stock_value",
		"date_field": None,
		"default_period": None,
		"filters": [["Bin", "actual_qty", "!=", 0]],
		"company_field": None,
		"dimensions": _bin_dims(),
		"unit": "currency",
		"examples": ["stock value by warehouse", "قيمة المخزون"],
	},
]


REQUIRED_KEYS = ("key", "label", "description", "doctype", "aggregate", "unit", "dimensions")


def normalize_metric(metric: dict) -> dict:
	"""Return a copy of `metric` with defaults filled in. Raises ValueError if it is invalid."""
	m = copy.deepcopy(metric)
	missing = [k for k in REQUIRED_KEYS if not m.get(k) and k != "dimensions"]
	if missing:
		raise ValueError(f"Metric {m.get('key')!r} is missing {', '.join(missing)}")
	m.setdefault("table", m["doctype"])
	m["table"] = m["table"] or m["doctype"]
	m.setdefault("field", None)
	m.setdefault("sign", 1)
	m.setdefault("date_field", None)
	m.setdefault("date_is_datetime", False)
	m.setdefault("default_period", None)
	m.setdefault("filters", [])
	m.setdefault("company_field", None)
	m.setdefault("dimensions", {})
	m.setdefault("examples", [])

	if m["aggregate"] not in AGGREGATES:
		raise ValueError(f"Metric {m['key']!r}: aggregate must be one of {AGGREGATES}")
	if m["aggregate"] == "sum" and not m["field"]:
		raise ValueError(f"Metric {m['key']!r}: a sum needs a field")
	if m["unit"] not in UNITS:
		raise ValueError(f"Metric {m['key']!r}: unit must be one of {UNITS}")
	if m["sign"] not in (1, -1):
		raise ValueError(f"Metric {m['key']!r}: sign must be 1 or -1")
	if m["default_period"] and not m["date_field"]:
		raise ValueError(f"Metric {m['key']!r}: default_period needs a date_field")
	for f in m["filters"]:
		if len(f) != 4 or f[2] not in OPERATORS:
			raise ValueError(f"Metric {m['key']!r}: bad filter {f!r}")
	for key, d in m["dimensions"].items():
		if key in DATE_GROUPS:
			raise ValueError(f"Metric {m['key']!r}: dimension {key!r} clashes with a date grouping")
		if not d.get("table") or not d.get("field"):
			raise ValueError(f"Metric {m['key']!r}: dimension {key!r} needs table and field")
		d.setdefault("link", None)
		d.setdefault("label", DIMENSION_LABELS.get(key, key.replace("_", " ").title()))
	tables = {m["doctype"], m["table"]} | {d["table"] for d in m["dimensions"].values()}
	if len(tables - {m["doctype"]}) > 1:
		raise ValueError(f"Metric {m['key']!r}: only one child table can be used")
	return m


def build_catalog(extra_metrics: list[dict] | None = None, disabled: set[str] | None = None) -> dict:
	"""Merge built-in and extra metrics (later definitions replace earlier ones by key)."""
	catalog = {}
	for metric in [*BUILTIN_METRICS, *(extra_metrics or [])]:
		m = normalize_metric(metric)
		catalog[m["key"]] = m
	for key in disabled or ():
		catalog.pop(key, None)
	return catalog
