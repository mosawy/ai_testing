# ai_testing

Frappe app for ERPNext **v15** (Python 3.10/3.11 on the bench): "Ask ERP", a question
answering assistant with charts. Developer: Mohamed Elsawy (Go Live Solutions). He speaks
Egyptian Arabic; reply to him in Arabic, keep code, comments and commit messages in English.

## Design rules (decided 2026-11-02)

- Answer questions and draw charts only. No writes, no actions, no agent loop.
- The model never writes SQL and never writes numbers. One model call turns the question
  into a JSON plan (metric from the catalog, grouping, period, filter values); the server
  validates it and runs `frappe.get_list` as the user. Never use raw SQL or
  `ignore_permissions` on business documents.
- Accuracy comes from the metric catalog (`assistant/catalog.py`): each metric is a reviewed
  definition (DocType, value field, fixed filters, date field, allowed dimensions).
  Questions outside it are answered "cannot answer yet" and logged; grow the catalog from
  the AI Question Log.
- Before adding a field to a metric, confirm it exists in v15:
  `https://raw.githubusercontent.com/frappe/erpnext/version-15/erpnext/<module>/doctype/<name>/<name>.json`.
- v15 `frappe.get_list` allows only some SQL functions in fields/group by (sum, count, date,
  ...; list `ALLOWED_SQL_FUNCTIONS` in `frappe/model/db_query.py` version-15) and silently
  drops fields the user cannot read, so the executor checks field permissions first.
- Track model quality with `assistant/eval_questions.json` and
  `bench --site <site> execute ai_testing.engine.api.run_evaluation`.

## No bench in the cloud session

```bash
python3 -m unittest ai_testing.assistant.test_assistant
find . -name "*.py" -exec python3 -m py_compile {} \;
for f in $(find . -name "*.js"); do node --check "$f"; done
for f in $(find . -name "*.json"); do python3 -c "import json; json.load(open('$f'))"; done
```

Keep logic in pure modules (`ai_testing/assistant/`, no `import frappe`) so it is testable.
Frappe NOT NULL rule: Int/Float/Check columns never accept NULL; Int left blank saves 0, so
treat 0 as "use default". The user installs on his VM (`bench migrate`,
`bench build --app ai_testing`, `bench restart`) and reports errors back. Background jobs did
not run on his VM; anything long must be runnable with `bench --site <site> execute`.

## Layout

- `ai_testing/assistant/` (pure): `catalog.py` metrics, `periods.py` presets and date
  buckets, `plan.py` parse and validate the model's JSON, `prompt.py`, `results.py` rows,
  top N + Others, comparison, chart choice, `evaluate.py` + `eval_questions.json`, tests.
- `ai_testing/engine/` (frappe): `settings.py`, `llm.py` (requests, OpenAI-compatible),
  `executor.py` (get_list, permission checks, link value resolution, tree expansion),
  `api.py` (`ask`, `get_metrics`, `test_connection`, `run_evaluation`).
- DocTypes (module AI Testing): AI Assistant Settings (single, System Manager),
  AI Question Log (System Manager, written by the server).
- Page `ask-erp` (`ai_testing/ai_testing/page/ask_erp/`), frappe.Chart; Arabic strings in
  `translations/ar.csv`.
- Hook `ai_assistant_metrics` lets other apps add metrics.

## Status

v1 written 2026-11-02, not yet installed on the VM.
