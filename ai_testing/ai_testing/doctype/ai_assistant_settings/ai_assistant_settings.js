// Copyright (c) 2026, Go Live Solutions and contributors
// For license information, please see license.txt

const AI_DEFAULT_BASE_URLS = {
	OpenAI: "https://api.openai.com/v1",
	Groq: "https://api.groq.com/openai/v1",
	SovereignEG: "https://backend.sovereigneg.com/v1",
	Ollama: "http://localhost:11434/v1",
};

frappe.ui.form.on("AI Assistant Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the settings first."));
				return;
			}
			frappe.call({
				method: "ai_testing.engine.api.test_connection",
				freeze: true,
				freeze_message: __("Calling the model..."),
				callback(r) {
					const res = r.message || {};
					frappe.msgprint({
						title: res.ok ? __("Connection OK") : __("Connection Failed"),
						indicator: res.ok ? "green" : "red",
						message: res.ok
							? __("Model {0} answered in {1} ms.", [
									frappe.utils.escape_html(res.model),
									res.latency_ms,
							  ])
							: frappe.utils.escape_html(res.error || __("Unknown error")),
					});
				},
			});
		});
		frm.add_custom_button(__("Open Assistant"), () => frappe.set_route("ask-erp"));
		render_metrics(frm);
	},

	provider(frm) {
		const url = AI_DEFAULT_BASE_URLS[frm.doc.provider];
		if (url) frm.set_value("base_url", url);
	},
});

function render_metrics(frm) {
	frappe.call({
		method: "ai_testing.engine.api.get_metrics",
		args: { include_disabled: 1 },
		callback(r) {
			const metrics = r.message || [];
			const esc = frappe.utils.escape_html;
			const rows = metrics
				.map(
					(m) => `<tr>
						<td><code>${esc(m.key)}</code></td>
						<td>${esc(__(m.label))}${m.disabled ? ` <span class="text-muted">(${__("disabled")})</span>` : ""}</td>
						<td>${esc(m.doctype)}</td>
						<td class="text-muted small">${esc(m.description)}</td>
					</tr>`
				)
				.join("");
			frm.fields_dict.metrics_html.$wrapper.html(`
				<table class="table table-bordered table-sm small">
					<thead><tr><th>${__("Key")}</th><th>${__("Metric")}</th><th>${__("Source")}</th><th>${__("Definition")}</th></tr></thead>
					<tbody>${rows}</tbody>
				</table>`);
		},
	});
}
