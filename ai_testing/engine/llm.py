"""Minimal OpenAI-compatible chat client (requests, no SDK).

Works with OpenAI, Groq, SovereignEG, Ollama and any server exposing
POST {base_url}/chat/completions. Error messages never contain the API key.
"""

from __future__ import annotations

import re
import time

import requests
from frappe import _

TIMEOUT_SECONDS = 60
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_DELAY_SECONDS = 2


class LLMError(Exception):
	def __init__(self, message: str, status_code: int | None = None):
		super().__init__(message)
		self.message = message
		self.status_code = status_code


def _redact(text: str, api_key: str) -> str:
	text = str(text or "")
	if api_key:
		text = text.replace(api_key, "***")
	return re.sub(r"(sk|gsk)-[A-Za-z0-9_\-]{8,}", "***", text)


def _error_detail(response) -> str:
	try:
		body = response.json()
	except ValueError:
		return (response.text or "")[:300]
	err = body.get("error") if isinstance(body, dict) else None
	if isinstance(err, dict):
		return str(err.get("message") or err)[:300]
	return str(err or body)[:300]


def _status_message(status: int) -> str:
	if status == 401:
		return _("The model provider rejected the API key (401).")
	if status == 403:
		return _("The model provider refused access (403). Check the key and the model.")
	if status == 404:
		return _("Model or endpoint not found (404). Check Base URL and Model in AI Assistant Settings.")
	if status == 429:
		return _("The model provider's rate limit or quota was reached (429).")
	if status >= 500:
		return _("The model provider is unavailable ({0}).").format(status)
	return _("The model provider rejected the request ({0}).").format(status)


def chat(settings, messages: list[dict], max_tokens: int = 700, json_mode: bool | None = None) -> str:
	"""Send messages and return the reply text."""
	if not settings.base_url or not settings.model:
		raise LLMError(_("Set Base URL and Model in AI Assistant Settings."))
	json_mode = settings.json_mode if json_mode is None else json_mode
	payload = {
		"model": settings.model,
		"messages": messages,
		"temperature": 0,
		"max_tokens": max_tokens,
	}
	if json_mode:
		payload["response_format"] = {"type": "json_object"}
	headers = {"Content-Type": "application/json"}
	if settings.api_key:
		headers["Authorization"] = f"Bearer {settings.api_key}"
	url = f"{settings.base_url}/chat/completions"

	for attempt in range(2):
		try:
			response = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS)
		except requests.Timeout:
			raise LLMError(_("The model did not answer within {0} seconds.").format(TIMEOUT_SECONDS))
		except requests.RequestException as e:
			raise LLMError(
				_("Could not reach the model server: {0}").format(_redact(type(e).__name__, settings.api_key))
			)

		if response.status_code == 400 and "response_format" in payload:
			# Some models / servers do not support JSON mode: retry once without it
			payload.pop("response_format")
			continue
		if response.status_code in RETRY_STATUSES and attempt == 0:
			time.sleep(RETRY_DELAY_SECONDS)
			continue
		break

	if response.status_code != 200:
		detail = _redact(_error_detail(response), settings.api_key)
		message = _status_message(response.status_code)
		raise LLMError(f"{message} {detail}".strip(), response.status_code)

	try:
		choice = response.json()["choices"][0]
		content = (choice.get("message") or {}).get("content") or ""
	except (ValueError, KeyError, IndexError, TypeError):
		raise LLMError(_("The model server returned an unexpected response."))
	if not content.strip():
		if choice.get("finish_reason") == "length":
			raise LLMError(_("The model ran out of tokens before answering. Try another model."))
		raise LLMError(_("The model returned an empty reply."))
	return content
