// Copyright (c) 2026, Go Live Solutions and contributors
// For license information, please see license.txt

frappe.pages["ask-erp"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Ask ERP"),
		single_column: true,
	});
	wrapper.ask_erp = new AskERP(page);
};

const NONE_KEY = "__none__";
const OTHERS_KEY = "__others__";
const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

const NOTE_TEXT = {
	period_ignored: () => __("This metric has no date, so the period was ignored."),
	compare_dropped: () => __("A comparison needs a period and no date grouping, so it was skipped."),
	truncated_groups: () => __("There are too many groups; only the first 20,000 were read."),
	truncated_buckets: () => __("Only the latest periods are shown."),
	all_companies: () => __("No default company is set for you, so all companies are added together."),
	party_currency: () => __("Outstanding amounts are in each party's account currency."),
};

class AskERP {
	constructor(page) {
		this.page = page;
		this.previous = null; // {question, plan} of the last answer, for follow-ups
		this.pending = false;
		this.counter = 0;
		this.make();
		this.load_examples();
	}

	make() {
		this.page.set_secondary_action(__("New Conversation"), () => this.reset(), "refresh");
		if (frappe.user.has_role("System Manager")) {
			this.page.add_menu_item(__("Settings"), () => frappe.set_route("Form", "AI Assistant Settings"));
			this.page.add_menu_item(__("Question Log"), () => frappe.set_route("List", "AI Question Log"));
		}
		this.$body = $(`
			<div class="ask-erp">
				<div class="ask-erp-messages"></div>
				<div class="ask-erp-composer">
					<textarea class="form-control ask-erp-input" rows="2"
						placeholder="${esc(__("Ask about sales, customers, purchases, payments or stock..."))}"></textarea>
					<button class="btn btn-primary ask-erp-send">${esc(__("Ask"))}</button>
				</div>
				<div class="ask-erp-hint text-muted">${esc(
					__("Numbers come from your ERP data with your permissions. The AI only reads the question.")
				)}</div>
			</div>`).appendTo(this.page.main);
		this.$messages = this.$body.find(".ask-erp-messages");
		this.$input = this.$body.find(".ask-erp-input");
		this.$send = this.$body.find(".ask-erp-send");

		this.$send.on("click", () => this.submit());
		this.$input.on("keydown", (e) => {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				this.submit();
			}
		});
		this.show_empty_state();
	}

	reset() {
		this.previous = null;
		this.$messages.empty();
		this.show_empty_state();
		this.$input.val("").focus();
	}

	show_empty_state() {
		this.$empty = $(`
			<div class="ask-erp-empty">
				<div class="ask-erp-empty-title">${esc(__("What would you like to know?"))}</div>
				<div class="ask-erp-examples"></div>
				<a class="ask-erp-metrics-link">${esc(__("What can I ask?"))}</a>
			</div>`).appendTo(this.$messages);
		this.$empty.find(".ask-erp-metrics-link").on("click", () => this.show_metrics());
		this.render_examples();
	}

	load_examples() {
		frappe
			.xcall("ai_testing.engine.api.get_metrics")
			.then((metrics) => {
				this.metrics = metrics || [];
				this.render_examples();
			})
			.catch(() => {
				this.metrics = [];
			});
	}

	render_examples() {
		if (!this.$empty || !this.metrics) return;
		const examples = this.metrics.map((m) => m.examples[0]).filter(Boolean).slice(0, 8);
		const $wrap = this.$empty.find(".ask-erp-examples").empty();
		examples.forEach((text) => {
			$(`<button class="btn btn-default btn-sm ask-erp-chip"></button>`)
				.text(text)
				.on("click", () => this.ask(text))
				.appendTo($wrap);
		});
	}

	show_metrics() {
		const rows = (this.metrics || [])
			.map(
				(m) => `<tr><td><b>${esc(__(m.label))}</b><div class="text-muted small">${esc(
					m.description
				)}</div></td><td class="small">${esc(m.examples.join(" | "))}</td></tr>`
			)
			.join("");
		frappe.msgprint({
			title: __("What I can answer"),
			wide: true,
			message: `<table class="table table-sm"><thead><tr><th>${esc(__("Metric"))}</th><th>${esc(
				__("Example questions")
			)}</th></tr></thead><tbody>${rows}</tbody></table>`,
		});
	}

	submit() {
		const question = (this.$input.val() || "").trim();
		if (!question || this.pending) return;
		this.$input.val("");
		this.ask(question);
	}

	ask(question, plan = null) {
		if (this.pending) return;
		if (this.$empty) {
			this.$empty.remove();
			this.$empty = null;
		}
		if (!plan) this.add_user_message(question);
		const $card = this.add_assistant_card();
		$card.html(`<div class="text-muted">${esc(__("Working on it..."))}</div>`);
		this.set_pending(true);

		const args = { question };
		if (plan) args.plan = JSON.stringify(plan);
		else if (this.previous) args.previous = JSON.stringify(this.previous);

		frappe
			.xcall("ai_testing.engine.api.ask", args)
			.then((res) => this.render_response($card, res))
			.catch((err) => {
				const message = (err && err.message) || __("The request failed.");
				$card.html(`<div class="ask-erp-error">${esc(message)}</div>`);
			})
			.finally(() => {
				this.set_pending(false);
				this.scroll();
			});
	}

	set_pending(value) {
		this.pending = value;
		this.$send.prop("disabled", value);
	}

	add_user_message(text) {
		$(`<div class="ask-erp-user"><div class="ask-erp-bubble"></div></div>`)
			.find(".ask-erp-bubble")
			.text(text)
			.end()
			.appendTo(this.$messages);
		this.scroll();
	}

	add_assistant_card() {
		return $(`<div class="ask-erp-card"></div>`).appendTo(this.$messages);
	}

	scroll() {
		const el = this.$messages.get(0);
		if (el) el.scrollTop = el.scrollHeight;
	}

	render_response($card, res) {
		res = res || {};
		const status = res.status;
		if (status === "answered") {
			this.previous = { question: res.question, plan: res.plan };
			this.render_answer($card, res);
		} else if (status === "clarify") {
			this.render_clarify($card, res);
		} else if (status === "chat") {
			$card.html(`<div class="ask-erp-text"></div>`).find(".ask-erp-text").text(res.message || "");
		} else if (status === "unsupported") {
			$card.html(`
				<div class="ask-erp-text">${esc(__("I cannot answer this one yet."))}</div>
				${res.message ? `<div class="text-muted small">${esc(res.message)}</div>` : ""}
				<a class="ask-erp-metrics-link small">${esc(__("What can I ask?"))}</a>`);
			$card.find(".ask-erp-metrics-link").on("click", () => this.show_metrics());
		} else {
			$card.html(`<div class="ask-erp-error">${esc(res.message || __("Something went wrong."))}</div>`);
		}
	}

	render_clarify($card, res) {
		$card.html(`<div class="ask-erp-text">${esc(res.message)}</div><div class="ask-erp-options"></div>`);
		const $options = $card.find(".ask-erp-options");
		(res.options || []).forEach((opt) => {
			const label = opt.label && opt.label !== opt.value ? `${opt.label} (${opt.value})` : opt.value;
			$(`<button class="btn btn-default btn-sm ask-erp-chip"></button>`)
				.text(label)
				.on("click", () => {
					const plan = JSON.parse(JSON.stringify(res.plan));
					plan.filters[res.dimension] = plan.filters[res.dimension].map((v) =>
						v === res.value ? opt.value : v
					);
					$options.find("button").prop("disabled", true);
					this.ask(res.question, plan);
				})
				.appendTo($options);
		});
	}

	// --- answer --------------------------------------------------------------------

	format(value, res) {
		const unit = res.metric.unit;
		if (value == null) return "";
		if (unit === "currency") {
			return res.currency ? format_currency(value, res.currency) : format_number(value, null, 2);
		}
		if (unit === "qty") return format_number(value, null, Number.isInteger(value) ? 0 : 3);
		return format_number(value, null, 0);
	}

	row_label(row, res) {
		if (row.key === NONE_KEY) return __("Not Set");
		if (row.key === OTHERS_KEY) return __("Others ({0})", [row.count]);
		return row.label || row.key;
	}

	period_text(period) {
		if (!period) return __("All dates");
		const range =
			period.from === period.to
				? frappe.datetime.str_to_user(period.from)
				: `${frappe.datetime.str_to_user(period.from)} – ${frappe.datetime.str_to_user(period.to)}`;
		return period.label ? `${__(period.label)} (${range})` : range;
	}

	change_badge(pct) {
		if (pct == null) return "";
		const cls = pct > 0 ? "up" : pct < 0 ? "down" : "";
		const sign = pct > 0 ? "+" : "";
		return `<span class="ask-erp-change ${cls}">${sign}${esc(format_number(pct, null, 1))}%</span>`;
	}

	render_answer($card, res) {
		const a = res.answer;
		const basis = res.basis;
		const id = `ask-erp-chart-${++this.counter}`;
		const group = res.group;
		const title = group
			? __("{0} by {1}", [__(res.metric.label), __(group.label)])
			: __(res.metric.label);

		let headline = `<div class="ask-erp-total">${esc(this.format(a.total, res))}</div>`;
		if (a.previous_total != null) {
			headline += `<div class="ask-erp-previous text-muted">${esc(__("Previous period"))}: ${esc(
				this.format(a.previous_total, res)
			)} ${this.change_badge(a.change_pct)}</div>`;
		}

		$card.html(`
			<div class="ask-erp-title">${esc(title)}</div>
			<div class="ask-erp-period text-muted">${esc(this.period_text(basis.period))}</div>
			${headline}
			${res.explanation ? `<div class="ask-erp-comment"><span class="ask-erp-comment-tag">${esc(__("AI comment"))}</span> ${esc(res.explanation)}</div>` : ""}
			<div class="ask-erp-chart" id="${id}"></div>
			<div class="ask-erp-table"></div>
			<div class="ask-erp-actions"></div>
			<div class="ask-erp-basis"></div>`);

		this.render_table($card.find(".ask-erp-table"), res);
		this.render_basis($card.find(".ask-erp-basis"), res);
		this.render_actions($card, res, id);
		if (a.chart) this.draw_chart(id, res, a.chart.type);
		else $card.find(".ask-erp-chart").remove();
	}

	render_table($wrap, res) {
		const a = res.answer;
		const rows = [...a.rows];
		if (a.others) rows.push(a.others);
		if (!rows.length) {
			if (a.kind !== "total") $wrap.html(`<div class="text-muted">${esc(__("No data for this question."))}</div>`);
			return;
		}
		const compare = rows.some((r) => r.previous != null);
		const link = res.group && res.group.kind === "dimension" ? res.group.link : null;
		const body = rows
			.map((r) => {
				let label = esc(this.row_label(r, res));
				if (link && r.key !== NONE_KEY && r.key !== OTHERS_KEY) {
					label = `<a href="${esc(frappe.utils.get_form_link(link, r.key))}">${label}</a>`;
				}
				return `<tr>
					<td>${label}</td>
					<td class="text-right">${esc(this.format(r.value, res))}</td>
					${compare ? `<td class="text-right">${esc(this.format(r.previous, res))}</td><td class="text-right">${this.change_badge(r.change_pct)}</td>` : ""}
				</tr>`;
			})
			.join("");
		$wrap.html(`
			<table class="table table-sm ask-erp-results">
				<thead><tr>
					<th>${esc(__(res.group ? res.group.label : ""))}</th>
					<th class="text-right">${esc(__(res.metric.label))}</th>
					${compare ? `<th class="text-right">${esc(__("Previous"))}</th><th class="text-right">${esc(__("Change"))}</th>` : ""}
				</tr></thead>
				<tbody>${body}</tbody>
			</table>`);
	}

	render_basis($wrap, res) {
		const b = res.basis;
		const parts = [`${esc(__("Source"))}: ${esc(__(b.source))}`];
		parts.push(`${esc(__("Period"))}: ${esc(this.period_text(b.period))}${
			b.period_source === "default" ? ` <span class="text-muted">(${esc(__("default"))})</span>` : ""
		}`);
		if (b.previous_period) parts.push(`${esc(__("Compared with"))}: ${esc(this.period_text(b.previous_period))}`);
		if (b.company) parts.push(`${esc(__("Company"))}: ${esc(b.company)}`);
		(b.filters || []).forEach((f) => {
			parts.push(
				`${esc(__(f.label))}: ${esc(f.values.join(", "))}${
					f.with_children ? ` <span class="text-muted">(${esc(__("including sub-groups"))})</span>` : ""
				}`
			);
		});
		const notes = (b.notes || [])
			.filter((n) => NOTE_TEXT[n])
			.map((n) => `<div class="ask-erp-note">${esc(NOTE_TEXT[n]())}</div>`)
			.join("");
		$wrap.html(`
			<div class="ask-erp-basis-line">${parts.join(" · ")}</div>
			<div class="ask-erp-definition text-muted">${esc(b.definition)}</div>
			${notes}`);
	}

	render_actions($card, res, chart_id) {
		const $actions = $card.find(".ask-erp-actions");
		const a = res.answer;
		if (a.chart) {
			const types = a.kind === "date" ? ["line", "bar"] : a.chart.datasets.length > 1 ? ["bar"] : ["bar", "donut", "pie"];
			if (types.length > 1) {
				types.forEach((type) => {
					$(`<button class="btn btn-xs btn-default"></button>`)
						.text(__({ line: "Line", bar: "Bar", donut: "Donut", pie: "Pie" }[type]))
						.on("click", () => this.draw_chart(chart_id, res, type))
						.appendTo($actions);
				});
			}
		}
		const plan = res.plan;
		if (plan && plan.period && !plan.compare_previous && (!res.group || res.group.kind !== "date")) {
			$(`<button class="btn btn-xs btn-default"></button>`)
				.text(__("Compare with previous period"))
				.on("click", () => this.ask(res.question, { ...plan, compare_previous: true }))
				.appendTo($actions);
		}
	}

	draw_chart(id, res, type) {
		const a = res.answer;
		const el = document.getElementById(id);
		if (!el || !a.chart) return;
		el.innerHTML = "";
		const dataset_name = {
			current: __(res.metric.label),
			previous: __("Previous period"),
		};
		const labels = a.chart.labels.map((label) => {
			if (label === NONE_KEY) return __("Not Set");
			if (label === OTHERS_KEY) return __("Others ({0})", [a.others ? a.others.count : ""]);
			return String(label);
		});
		new frappe.Chart(el, {
			type,
			height: 260,
			data: {
				labels,
				datasets: a.chart.datasets.map((d) => ({ name: dataset_name[d.key] || d.key, values: d.values })),
			},
			axisOptions: { xIsSeries: a.kind === "date" ? 1 : 0, shortenYAxisNumbers: 1 },
			lineOptions: { regionFill: 1 },
			tooltipOptions: { formatTooltipY: (v) => this.format(v, res) },
		});
	}
}
