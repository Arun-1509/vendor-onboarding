"""The Gemini (free-tier) transport, driven by a FAKE http client: request shape, schema conversion,
retry and model fallback, and that unusable answers raise (-> human hold) rather than guess."""
import json
from types import SimpleNamespace as NS

import pytest

from app import config
from app.llm import GeminiLLM, LLMError, _gemini_schema


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(config, "GEMINI_MODEL", "m-main")
    monkeypatch.setattr(config, "GEMINI_JUDGE_MODEL", "m-judge")
    monkeypatch.setattr(config, "GEMINI_FALLBACK", "m-fallback")
    monkeypatch.setattr("time.sleep", lambda s: None)


def ok(payload):
    return NS(status_code=200, text="", json=lambda: {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]})


def status(code):
    return NS(status_code=code, text="err", json=lambda: {})


class FakeHttp:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def post(self, url, params=None, json=None):
        self.calls.append((url.split("/models/")[1].split(":")[0], json))
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(script):
    return GeminiLLM(client=FakeHttp(script))


def test_schema_conversion_removes_type_lists():
    src = {"type": "object", "additionalProperties": False, "properties": {
        "v": {"type": ["string", "null"]}, "c": {"type": "number", "minimum": 0, "maximum": 1}}}
    out = _gemini_schema(src)
    assert out["properties"]["v"] == {"type": "string", "nullable": True}
    assert "additionalProperties" not in out and out["properties"]["c"]["maximum"] == 1


def test_judgment_parses_and_uses_judge_model():
    out = {"same_entity": "yes", "confidence": 0.9, "variant_type": "abbreviation", "reasoning": "x"}
    llm = make([ok(out)])
    assert llm.judge_names("A", "B", {}) == out
    model, body = llm.http.calls[0]
    assert model == "m-judge" and body["generationConfig"]["responseMimeType"] == "application/json"


def test_rate_limit_retries_then_falls_back():
    out = {"subject": "s", "body": "b"}
    llm = make([status(429), status(429), ok(out)])
    assert llm.draft_email("vendor", "request", {"asks": []}) == out
    assert [c[0] for c in llm.http.calls] == ["m-main", "m-main", "m-fallback"]


def test_pdf_is_sent_inline(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    llm = make([ok({"doc_type_detected": "tax_cert", "legible": True, "fields": {}})])
    llm.extract_document("tax_cert", str(pdf))
    parts = llm.http.calls[0][1]["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "application/pdf" and "text" in parts[1]


@pytest.mark.parametrize("bad", [NS(status_code=200, text="", json=lambda: {"candidates": []}),
                                 NS(status_code=200, text="", json=lambda: {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}),
                                 status(400)])
def test_unusable_answer_raises_not_guesses(bad):
    with pytest.raises(LLMError):
        make([bad, bad, bad, bad]).judge_names("A", "B", {})


def test_provider_selection(monkeypatch):
    import importlib
    import os
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("MOCK_LLM", "auto")
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    importlib.reload(config)
    assert config.PROVIDER == "gemini" and config.MOCK_LLM is False
    monkeypatch.delenv("GEMINI_API_KEY")
    importlib.reload(config)
    assert config.MOCK_LLM is True

    monkeypatch.undo()
    importlib.reload(config)       # leave config as the environment defines it
