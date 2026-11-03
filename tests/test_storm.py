from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from paperdesk.storm import (
    EvidenceIndex,
    ReviewCancelled,
    ReviewError,
    ReviewSettings,
    StormEngine,
    chunk_pages,
)


PAPER = "The authors compare an intervention to a randomized control group of 48 participants. The primary endpoint is accuracy. No causal effect beyond this study is established."
FINAL = """## Core Contributions & Novelty
The authors report a controlled comparison [P1-C1]. External novelty is unverified.
## Noteworthy Research Questions
How would the results generalize beyond the reported participants [P1-C1]?
## Critical Limitations
The reported sample comprises 48 participants [P1-C1]."""


class FakeClient:
    def __init__(self, outputs=None, on_call=None):
        self.outputs = list(outputs or [
            "Assess controlled design and external validity [P1-C1].",
            "Is the control randomized and what is the sample size [P1-C1]?",
            "The paper reports randomized controls and 48 participants [P1-C1].",
            FINAL,
        ])
        self.calls = []
        self.on_call = on_call
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.on_call:
            self.on_call(len(self.calls))
        output = self.outputs.pop(0)
        if isinstance(output, tuple):
            content, reason = output
        else:
            content, reason = output, "stop"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=reason)])


def engine(client, **settings):
    return StormEngine("", ReviewSettings(rounds=1, **settings), client=client)


def test_chunk_ids_are_deterministic_and_page_specific():
    pages = ["Alpha " * 500, "", "Second page."]
    first = chunk_pages(pages)
    assert first == chunk_pages(pages)
    assert first[0].identifier == "P1-C1"
    assert first[-1].identifier == "P3-C1"
    assert all(len(chunk.text) <= 1400 for chunk in first)
    assert " ".join(chunk.text for chunk in first if chunk.page == 1).split() == pages[0].split()


def test_retrieval_uses_relevant_content_and_fallback_covers_end():
    chunks = chunk_pages(["Methods include randomization and blinded controls.", "Participants lived in urban areas.", "Limitations include missing outcomes."])
    index = EvidenceIndex(chunks)
    assert index.retrieve("randomization", 1)[0].page == 1
    assert index.retrieve("missing outcomes", 1)[0].page == 3
    assert [chunk.page for chunk in index.coverage(10)] == [1, 2, 3]
    assert index.retrieve("unmatchedkeyword", 3) == chunks
    assert index.retrieve("methods", 0) == []


def test_complete_pipeline_emits_grounded_review_and_appendix():
    client = FakeClient()
    progress = []
    result = engine(client).review([PAPER], "A trial", progress=progress.append)
    assert result.startswith("## Core Contributions")
    assert "### [P1-C1] — PDF page 1" in result
    assert "> " + PAPER in result
    assert "1 adversarial QA round(s)" in result
    assert len(client.calls) == 4
    assert len(progress) == 4
    assert client.calls[0]["model"] == "gpt-4.1-mini"
    assert all(call["max_completion_tokens"] == 1600 for call in client.calls)
    evidence = json.loads(client.calls[2]["messages"][1]["content"])["untrusted_evidence_json"]
    assert evidence[0]["id"] == "[P1-C1]"
    assert evidence[0]["text"] == PAPER


def test_multiple_rounds_run_independent_skeptic_and_specialist_calls():
    client = FakeClient(outputs=["Plan [P1-C1]", "Question?", "Answer [P1-C1]", "Follow-up?", "Follow-up answer [P1-C1]", FINAL])
    result = StormEngine("", ReviewSettings(rounds=2), client=client).review([PAPER], "Trial")
    assert len(client.calls) == 6
    second_skeptic = json.loads(client.calls[3]["messages"][1]["content"])
    assert "Answer [P1-C1]" in second_skeptic["previous_answer"]
    assert "2 adversarial QA round(s)" in result


def test_fabricated_source_identifier_is_rejected():
    client = FakeClient(outputs=["Plan", "Question?", "Answer [P1-C1]", FINAL.replace("[P1-C1]", "[P99-C99]")])
    with pytest.raises(ReviewError, match="absent from its context"):
        engine(client).review([PAPER], "Trial")


@pytest.mark.parametrize("bad_final", [
    FINAL.replace("## Critical Limitations", "## Other"),
    "## Core Contributions & Novelty\n## Noteworthy Research Questions\nQuestion [P1-C1].\n## Critical Limitations\nLimitation [P1-C1].",
    FINAL.replace("[P1-C1]", ""),
])
def test_incomplete_or_uncited_output_is_rejected(bad_final):
    client = FakeClient(outputs=["Plan", "Question?", "Answer [P1-C1]", bad_final])
    with pytest.raises(ReviewError):
        engine(client).review([PAPER], "Trial")


def test_cancellation_stops_calls_and_discards_response():
    state = {"cancelled": False}
    client = FakeClient(on_call=lambda _: state.update(cancelled=True))
    with pytest.raises(ReviewCancelled):
        engine(client).review([PAPER], "Trial", cancelled=lambda: state["cancelled"])
    assert len(client.calls) == 1


def test_cancelled_before_start_makes_no_provider_calls():
    client = FakeClient()
    with pytest.raises(ReviewCancelled):
        engine(client).review([PAPER], "Trial", cancelled=lambda: True)
    assert client.calls == []


def test_empty_and_overlong_papers_fail_before_provider_calls():
    client = FakeClient()
    with pytest.raises(ReviewError, match="no extracted text"):
        engine(client).review(["", " "], "Scan")
    with pytest.raises(ReviewError, match="character review limit"):
        engine(client, max_document_chars=1000).review(["x" * 1001], "Long")
    assert client.calls == []


def test_provider_truncated_response_is_not_saved_as_review():
    with pytest.raises(ReviewError, match="output limit"):
        engine(FakeClient(outputs=[("partial", "length")])).review([PAPER], "Trial")


def test_every_serialized_provider_payload_is_bounded():
    pages = [PAPER * 12] * 20
    client = FakeClient()
    engine(client, context_bytes=12000).review(pages, "Trial " * 500)
    assert all(len(call["messages"][1]["content"].encode("utf-8")) <= 12000 for call in client.calls)


def test_maximum_rounds_with_escape_heavy_dialogue_respect_minimum_context_budget():
    escaped_text = '\\"' * 5000
    outputs = [escaped_text + " [P1-C1]"]
    for _ in range(4):
        outputs.extend([escaped_text + " [P1-C1]", escaped_text + " [P1-C1]"])
    outputs.append(FINAL)
    client = FakeClient(outputs=outputs)
    settings = ReviewSettings(rounds=4, context_bytes=12000)
    result = StormEngine("", settings, client=client).review([PAPER * 12] * 20, '\\"' * 500)
    assert len(client.calls) == 10
    assert "4 adversarial QA round(s)" in result
    assert all(len(call["messages"][1]["content"].encode("utf-8")) <= 12000 for call in client.calls)


def test_malformed_evidence_identifiers_are_rejected():
    client = FakeClient(outputs=["Plan", "Question?", "Answer [P1-C1]", FINAL + " [P1-C1x]"])
    with pytest.raises(ReviewError, match="malformed evidence"):
        engine(client).review([PAPER], "Trial")


def test_close_releases_owned_client_once_and_prevents_reuse(monkeypatch):
    client = FakeClient()
    closed = []
    client.close = lambda: closed.append(True)
    monkeypatch.setattr("paperdesk.storm.OpenAI", lambda **kwargs: client)
    instance = StormEngine("test-key", ReviewSettings(rounds=1))
    instance.close()
    instance.close()
    assert closed == [True]
    with pytest.raises(ReviewError, match="closed"):
        instance.review([PAPER], "Trial")


def test_invalid_settings_are_rejected():
    for values in ({"rounds": 0}, {"rounds": 5}, {"model": ""}, {"max_output_tokens": 2}, {"context_bytes": 1}):
        with pytest.raises(ValueError):
            ReviewSettings(**values)
