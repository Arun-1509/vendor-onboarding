"""The real-Claude client can't be called without a key, so these tests drive it with a FAKE
Anthropic client to prove the control flow: structured parse, forced-tool fallback, model
fallback on refusal/outage, and that failures raise (-> STAGE_ERROR) instead of guessing."""
from types import SimpleNamespace as NS

import anthropic
import pytest

from app import config
from app.llm import AnthropicLLM, LLMError


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(config, "MODEL_WRITE", "claude-fable-5-1")
    monkeypatch.setattr(config, "MODEL_FALLBACK", "claude-sonnet-5-5")


def tool_resp(name, payload, stop="tool_use"):
    return NS(stop_reason=stop, content=[NS(type="tool_use", name=name, input=payload)])


class Fake:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append((kw["model"], kw["tool_choice"]["type"]))
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def llm_with(script):
    llm = AnthropicLLM()
    llm.client = Fake(script)
    return llm


def _bad_request():
    return anthropic.BadRequestError("nope", response=NS(status_code=400, request=NS(), headers={}), body=None)


def test_structured_judgment_parses(tmp_path):
    out = {"same_entity": "yes", "confidence": 0.9, "variant_type": "abbreviation", "reasoning": "x"}
    llm = llm_with([tool_resp("record_judgment", out)])
    assert llm.judge_names("A", "B", {}) == out
    assert llm.client.calls == [(config.MODEL_JUDGE, "tool")]


def test_forced_tool_rejected_retries_with_auto():
    out = {"same_entity": "no", "confidence": 0.7, "variant_type": "different_entity", "reasoning": "x"}
    llm = llm_with([_bad_request(), tool_resp("record_judgment", out)])
    assert llm.judge_names("A", "B", {})["same_entity"] == "no"
    assert [c[1] for c in llm.client.calls] == ["tool", "auto"]


def test_refusal_falls_back_to_second_model():
    out = {"subject": "s", "body": "b"}
    llm = llm_with([NS(stop_reason="refusal", content=[]), tool_resp("record_email", out)])
    assert llm.draft_email("vendor", "request", {"asks": []}) == out
    assert [c[0] for c in llm.client.calls] == ["claude-fable-5-1", "claude-sonnet-5-5"]


def test_total_failure_raises_not_guesses():
    llm = llm_with([NS(stop_reason="end_turn", content=[NS(type="text", text="I think...")])] * 4)
    with pytest.raises(LLMError):
        llm.judge_names("A", "B", {})


def test_document_extraction_sends_pdf_and_schema(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    payload = {"doc_type_detected": "tax_cert", "legible": True, "fields": {}}
    llm = llm_with([tool_resp("record_extraction", payload)])
    captured = {}
    orig = llm.client._create
    def spy(**kw):
        captured.update(kw)
        return orig(**kw)
    llm.client.messages.create = spy
    assert llm.extract_document("tax_cert", str(pdf)) == payload
    block = captured["messages"][0]["content"][0]
    assert block["type"] == "document" and block["source"]["media_type"] == "application/pdf"
    assert "legal_name" in captured["tools"][0]["input_schema"]["properties"]["fields"]["properties"]
