# Copyright (c) 2026, Go Live Solutions and contributors
# For license information, please see license.txt

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

DEFAULT_BASE_URLS = {
	"OpenAI": "https://api.openai.com/v1",
	"Groq": "https://api.groq.com/openai/v1",
	"SovereignEG": "https://backend.sovereigneg.com/v1",
	"Ollama": "http://localhost:11434/v1",
}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class AIAssistantSettings(Document):
	def validate(self):
		self.base_url = (self.base_url or DEFAULT_BASE_URLS.get(self.provider) or "").strip().rstrip("/")
		if self.base_url.endswith("/chat/completions"):
			self.base_url = self.base_url[: -len("/chat/completions")]
		parsed = urlparse(self.base_url)
		if parsed.scheme not in ("http", "https") or not parsed.hostname:
			frappe.throw(_("Base URL must be a full URL, e.g. https://api.openai.com/v1"))
		if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
			frappe.throw(_("Base URL must use https unless the model server runs on this machine."))
		self.model = (self.model or "").strip()

		from ai_testing.engine.settings import get_catalog

		unknown = [k for k in self.get_disabled_metrics() if k not in get_catalog(include_disabled=True)]
		if unknown:
			frappe.throw(_("Unknown metric keys in Disabled Metrics: {0}").format(", ".join(unknown)))

	def get_disabled_metrics(self) -> list[str]:
		return [line.strip() for line in (self.disabled_metrics or "").splitlines() if line.strip()]
