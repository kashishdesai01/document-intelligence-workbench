import json

import httpx
import pytest

from eval.baselines import ollama, text_support
from eval.metrics import fit_isotonic, reliability
from eval.runner import quality_gate
from eval.synth import statement
from workbench.models import Box, Page, Word


def test_quality_gate_rejects_an_unsafe_straight_through_document():
    safe = dict(
        failed=0,
        unsafe_straight_through_documents=0,
        document_accuracy=0.83,
        cell_f1=0.99,
        straight_through=0.83,
        auto_accepted_error_rate=0,
    )
    quality_gate({"variants": {"full": safe}})
    with pytest.raises(SystemExit, match="quality gates"):
        quality_gate({"variants": {"full": {**safe, "unsafe_straight_through_documents": 1}}})


def test_isotonic_pools_ties_and_decreasing_bins():
    blocks = fit_isotonic([(0.5, True), (0.5, True), (0.7, False), (0.9, True)])
    assert blocks == [
        {"upper": 0.7, "correct": 2, "count": 3},
        {"upper": 0.9, "correct": 1, "count": 1},
    ]
    assert reliability([(0.5, True), (0.5, False)])["ece"] == 0


@pytest.mark.parametrize("variant", ["text_only", "vision_llm"])
def test_local_model_adapter_keeps_modalities_separate(variant):
    _, truth = statement(27)
    word = Word(text="123.45", box=Box(left=0.1, top=0.1, right=0.3, bottom=0.2), score=1)
    page = Page(number=1, width=100, height=100, source="native", words=[word])

    def respond(request):
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["format"]["title"] == "Statement"
        payload = body["messages"][1]
        if variant == "vision_llm":
            assert payload["images"] == ["YWJj"]
            assert "123.45" not in payload["content"]
        else:
            assert "images" not in payload
            assert payload["content"] == "123.45"
        return httpx.Response(
            200,
            json={
                "message": {"content": json.dumps(truth)},
                "prompt_eval_count": 20,
                "eval_count": 40,
            },
        )

    with httpx.Client(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(respond)
    ) as client:
        answer = ollama(client, "test-local-model", variant, [page], [b"abc"])
    assert answer.statement.model_dump() == truth
    assert answer.prompt_tokens == 20
    assert text_support({"opening_balance": "123.45", "closing_balance": "123.46"}, [page]) == (
        1,
        2,
    )
