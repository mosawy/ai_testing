# AI Testing – Ask ERP

Ask ERPNext questions in plain language (Arabic or English) and get a number, a table
and a chart. Frappe / ERPNext **v15**.

## How it works

```
question ──► model (one call) ──► JSON plan ──► server validates ──► frappe.get_list ──► table + chart
```

- The model **never writes SQL and never writes numbers**. It only picks a metric from a
  fixed catalog, a grouping, a period and filter values, as JSON.
- The server checks the plan against the catalog, turns periods ("last month") into dates,
  finds the records the user named (customer, item, ...) and runs `frappe.get_list` as the
  current user: role permissions, User Permissions, owner-only rules and permlevel fields apply.
- Numbers on screen come from the database. Under every answer: source DocType, period,
  company, filters and the metric definition, so the user can check it.
- A question the catalog does not cover gets "I cannot answer this one yet" and is logged
  (**AI Question Log**, status Unsupported), so new metrics can be added from real questions.
- A chat reply from the model that contains digits is dropped (it could be invented).

## Metrics (v1)

Selling: net sales, invoice count, returns, item sales amount and quantity, receivables,
overdue receivables, sales orders booked, open sales orders (count and value), new customers.
Buying: net purchases, payables, purchase orders. Payments: received, made.
Stock: quantity and value (Bin).

Definitions: `ai_testing/assistant/catalog.py`, or AI Assistant Settings > Available Metrics.
Each metric lists the dimensions it can be grouped and filtered by. Tree masters (Territory,
Customer Group, Item Group, Warehouse, Cost Center) include their sub-groups when filtered.
When the user has a default company, company metrics are filtered to it.

Other apps can add metrics with a hook:

```python
# hooks.py of another app
ai_assistant_metrics = ["my_app.ai.get_metrics"]  # returns a list of metric dicts
```

## Install

```bash
bench get-app https://github.com/mosawy/ai_testing --branch claude/pensive-heisenberg-3iugzy
```

```bash
bench --site <site> install-app ai_testing
```

```bash
bench build --app ai_testing && bench restart
```

No extra Python packages: the model is called with `requests` (OpenAI-compatible API).

## Setup

1. **AI Assistant Settings**: Provider, Base URL, API Key, Model. Click **Test Connection**.
   - OpenAI `https://api.openai.com/v1`, e.g. `gpt-4o-mini`
   - Groq `https://api.groq.com/openai/v1`, e.g. `llama-3.3-70b-versatile`
   - SovereignEG `https://backend.sovereigneg.com/v1`
   - Ollama `http://localhost:11434/v1` (no key)
   - JSON Mode on; it is dropped automatically if the model rejects it.
   - Reference Date: answer as if today were that date (test copy with old data).
   - Comment on Results: off by default. On: totals and at most 20 groups go to the model
     for a one or two sentence comment, shown as "AI comment".
2. Open **/app/ask-erp**. Page roles (Sales, Purchase, Stock, Accounts users and managers,
   System Manager) can be changed on the Page record; the data is still limited by each
   user's own permissions.

## Measure the model before trusting it

`ai_testing/assistant/eval_questions.json` holds 37 questions (Egyptian / Gulf Arabic and
English) with the plan expected for each. This asks the model for every plan and scores it
(no data is read):

```bash
bench --site <site> execute ai_testing.engine.api.run_evaluation
```

Run it for every model you consider and after every prompt change. Add the questions your
users really ask (from AI Question Log) to the file.

## Test script

1. Settings saved, Test Connection OK.
2. "sales this month" → a total with currency, period = this month (default), company.
3. "مبيعات السنة دي شهر بشهر" → line chart, 12 months, empty months show 0.
4. "top 5 customers by sales last year" → donut or bar, table with customer names (links),
   "Others" row when there are more customers.
5. Click **Compare with previous period** → previous column and change %.
6. Type part of a customer name that matches several customers → buttons to pick one.
7. A territory that has sub-territories as a filter → "(including sub-groups)" in the basis line.
8. "what is our gross margin per item" → "I cannot answer this one yet", log status Unsupported.
9. Log in as a user without Sales Invoice access, ask about sales → permission message.
10. A user with a User Permission on one customer → sales only for that customer.
11. AI Question Log shows every question with status, metric, plan and duration.

## Development

Pure logic (no frappe import) is in `ai_testing/assistant/` and is unit-tested offline:

```bash
python3 -m unittest ai_testing.assistant.test_assistant
```
