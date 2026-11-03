"""Bounded, evidence-grounded, single-paper STORM-inspired review workflow."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import html
import json
import math
import re
from typing import Callable

from openai import OpenAI

from .prompts import (
    PERSPECTIVE_SYSTEM,
    SKEPTIC_SYSTEM,
    SPECIALIST_SYSTEM,
    SYNTHESIS_SYSTEM,
)


class ReviewError(RuntimeError):
    """The provider response could not support a valid grounded review."""


class ReviewCancelled(ReviewError):
    """Cancellation requested between provider calls."""


@dataclass(frozen=True)
class ReviewSettings:
    model: str = "gpt-4.1-mini"
    rounds: int = 2
    max_output_tokens: int = 1600
    context_bytes: int = 24000
    max_document_chars: int = 2_000_000

    def __post_init__(self) -> None:
        if not self.model.strip() or len(self.model) > 150:
            raise ValueError("A model identifier of 1–150 characters is required.")
        if not 1 <= self.rounds <= 4:
            raise ValueError("Review rounds must be between 1 and 4.")
        if not 600 <= self.max_output_tokens <= 4000:
            raise ValueError("Output token budget must be between 600 and 4000.")
        if not 12000 <= self.context_bytes <= 48000:
            raise ValueError("Context byte budget must be between 12000 and 48000.")
        if not 1000 <= self.max_document_chars <= 5_000_000:
            raise ValueError("Document character limit must be 1000–5000000.")


@dataclass(frozen=True)
class EvidenceChunk:
    identifier: str
    page: int
    text: str


_CITATION = re.compile(r"\[P\d+-C\d+\]")
_CITATION_LIKE = re.compile(r"\[[Pp]\d[^\]\n]{0,64}\]")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_STOP_WORDS = frozenset(
    "the a an and or to of in on for as by is are was were be been this that "
    "with from it its at their they we our paper study what which how does do "
    "did can could would should not into about these those than then also".split()
)
_HEADINGS = (
    "## Core Contributions & Novelty",
    "## Noteworthy Research Questions",
    "## Critical Limitations",
)


def _tokens(text: str) -> list[str]:
    return [word for word in _TOKEN.findall(text.lower()) if word not in _STOP_WORDS]


def _clip(text: str, budget: int) -> str:
    """A UTF-8 byte ceiling also bounds the input size for byte-level tokenizers."""
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text
    marker = "\n[truncated]"
    remaining = max(0, budget - len(marker.encode("utf-8")))
    return encoded[:remaining].decode("utf-8", errors="ignore") + marker


def chunk_pages(pages: list[str], chunk_chars: int = 1400) -> list[EvidenceChunk]:
    """Create stable page/chunk IDs, preserving page boundaries and paragraph text."""
    if chunk_chars < 200:
        raise ValueError("chunk_chars must be at least 200")
    chunks: list[EvidenceChunk] = []
    for page_no, page in enumerate(pages, 1):
        cleaned = re.sub(r"[ \t]+", " ", page.replace("\x00", ""))
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        offset = 0
        number = 1
        while offset < len(cleaned):
            end = min(offset + chunk_chars, len(cleaned))
            if end < len(cleaned):
                # Prefer a word/paragraph boundary without discarding any text.
                boundary = cleaned.rfind(" ", offset + chunk_chars // 2, end)
                if boundary > offset:
                    end = boundary
            text = cleaned[offset:end].strip()
            if text:
                chunks.append(EvidenceChunk(f"P{page_no}-C{number}", page_no, text))
                number += 1
            offset = end
    return chunks


class EvidenceIndex:
    """Small deterministic BM25 index with representative fallback coverage."""

    def __init__(self, chunks: list[EvidenceChunk]):
        self.chunks = chunks
        self._terms = [Counter(_tokens(chunk.text)) for chunk in chunks]
        self._lengths = [sum(terms.values()) for terms in self._terms]
        self._average_length = sum(self._lengths) / max(1, len(chunks)) or 1
        self._frequency: Counter[str] = Counter()
        for terms in self._terms:
            self._frequency.update(terms.keys())

    def retrieve(self, query: str, limit: int = 10) -> list[EvidenceChunk]:
        if not self.chunks or limit <= 0:
            return []
        terms = set(_tokens(query))
        scored: list[tuple[float, int]] = []
        count = len(self.chunks)
        for number, frequency in enumerate(self._terms):
            score = 0.0
            for term in terms:
                occurrences = frequency.get(term, 0)
                if not occurrences:
                    continue
                document_frequency = self._frequency[term]
                idf = math.log(1 + (count - document_frequency + 0.5) / (document_frequency + 0.5))
                length_ratio = self._lengths[number] / self._average_length
                score += idf * occurrences * 2.5 / (occurrences + 1.5 * (0.25 + 0.75 * length_ratio))
            scored.append((score, number))
        ranked = [number for score, number in sorted(scored, key=lambda pair: (-pair[0], pair[1])) if score > 0]
        # Ensure a query with no matches samples the document instead of its prefix.
        representative = self.coverage_indices(min(limit, count))
        ordered = list(dict.fromkeys(ranked + representative))
        return [self.chunks[number] for number in ordered[:limit]]

    def coverage_indices(self, limit: int = 10) -> list[int]:
        size = len(self.chunks)
        if not size or limit <= 0:
            return []
        count = min(limit, size)
        if count == 1:
            return [0]
        return [round(i * (size - 1) / (count - 1)) for i in range(count)]

    def coverage(self, limit: int = 10) -> list[EvidenceChunk]:
        return [self.chunks[number] for number in self.coverage_indices(limit)]


def _evidence(chunks: list[EvidenceChunk], budget: int) -> tuple[str, list[EvidenceChunk]]:
    """Serialize complete chunks; never label a silently truncated source chunk."""
    included: list[EvidenceChunk] = []
    items: list[dict[str, str | int]] = []
    for chunk in chunks:
        item = {"id": f"[{chunk.identifier}]", "page": chunk.page, "text": chunk.text}
        candidate = json.dumps(items + [item], ensure_ascii=False)
        if len(candidate.encode("utf-8")) > budget:
            continue
        included.append(chunk)
        items.append(item)
    return json.dumps(items, ensure_ascii=False), included


def _validate_citations(text: str, allowed: set[str], *, required: bool = False) -> set[str]:
    citations = set(_CITATION.findall(text))
    malformed = set(_CITATION_LIKE.findall(text)) - citations
    if malformed:
        raise ReviewError("The model returned malformed evidence identifiers: " + ", ".join(sorted(malformed)))
    unknown = citations - allowed
    if unknown:
        raise ReviewError("The model returned evidence identifiers absent from its context: " + ", ".join(sorted(unknown)))
    if required and not citations:
        raise ReviewError("The model returned an uncited review. Retry with a model that follows evidence instructions.")
    return citations


class StormEngine:
    """Run perspective planning, adversarial QA, and evidence-backed synthesis.

    Each call is non-streaming and bounded by a timeout/output limit. Cancellation
    is checked between calls, so an in-flight HTTPS request may take up to its
    timeout (and one retry) to finish. This method belongs on a worker thread.
    """

    def __init__(
        self,
        api_key: str,
        settings: ReviewSettings | None = None,
        client: object | None = None,
    ) -> None:
        self.settings = settings or ReviewSettings()
        if client is None and not api_key.strip():
            raise ValueError("Provide an OpenAI API key to run a review.")
        self.client = client if client is not None else OpenAI(api_key=api_key, timeout=60.0, max_retries=1)
        self._owns_client = client is None
        self._closed = False

    def close(self) -> None:
        """Release owned HTTP connections; an injected client remains caller-owned."""
        if self._owns_client and not self._closed:
            self.client.close()
        self._closed = True

    def _call(self, system: str, payload: dict, allowed: set[str], check: Callable[[], None]) -> str:
        check()
        # Keep excerpts as structured data, without an extra layer of JSON escaping.
        payload = dict(payload)
        payload["untrusted_evidence_json"] = json.loads(payload["untrusted_evidence_json"])
        content = json.dumps(payload, ensure_ascii=False)
        # A hard bound includes all JSON framing, not just source excerpts.
        if len(content.encode("utf-8")) > self.settings.context_bytes:
            raise ReviewError("Internal review context exceeded its configured byte budget.")
        response = self.client.chat.completions.create(
            model=self.settings.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
            max_completion_tokens=self.settings.max_output_tokens,
        )
        check()
        if not response.choices:
            raise ReviewError("The provider returned no review response.")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise ReviewError("The model reached its output limit. Use a larger output budget or a more concise model.")
        if choice.finish_reason != "stop":
            raise ReviewError("The provider interrupted or refused the review response.")
        text = choice.message.content
        if not isinstance(text, str) or not text.strip():
            raise ReviewError("The provider returned an empty or refused response.")
        _validate_citations(text, allowed)
        return text.strip()

    def review(
        self,
        pages: list[str],
        title: str,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> str:
        def check() -> None:
            if cancelled and cancelled():
                raise ReviewCancelled("Review cancelled.")

        def report(message: str) -> None:
            check()
            if progress:
                progress(message)

        check()
        if self._closed:
            raise ReviewError("This review engine is closed. Create a new engine to review another paper.")
        if not pages or not any(page.strip() for page in pages):
            raise ReviewError("The paper contains no extracted text. OCR a scanned PDF before reviewing it.")
        if sum(map(len, pages)) > self.settings.max_document_chars:
            raise ReviewError(f"Paper exceeds the {self.settings.max_document_chars:,}-character review limit.")
        chunks = chunk_pages(pages)
        if not chunks:
            raise ReviewError("The paper contains no usable text.")
        index = EvidenceIndex(chunks)
        budget = self.settings.context_bytes
        safe_title = _clip(title, 400)
        seen: dict[str, EvidenceChunk] = {}

        def evidence_for(candidates: list[EvidenceChunk], size: int) -> tuple[str, set[str]]:
            serialized, included = _evidence(candidates, size)
            if not included:
                raise ReviewError("No complete evidence chunk fits in the context budget.")
            seen.update((f"[{chunk.identifier}]", chunk) for chunk in included)
            return serialized, {f"[{chunk.identifier}]" for chunk in included}

        report("Phase 1/3: assigning perspectives and planning questions…")
        overview, overview_ids = evidence_for(index.coverage(10), budget // 2)
        plan = self._call(PERSPECTIVE_SYSTEM, {
            "task": "Plan independent methodological and domain perspectives.",
            "paper_title": safe_title,
            "untrusted_evidence_json": overview,
        }, overview_ids, check)
        plan = _clip(plan, budget // 12)
        dialogue: list[dict[str, str | int]] = []
        previous = "No previous answers. Begin with the most consequential methodological questions."

        for turn in range(1, self.settings.rounds + 1):
            report(f"Phase 2/3: skeptic questions, round {turn}/{self.settings.rounds}…")
            questions = self._call(SKEPTIC_SYSTEM, {
                "paper_title": safe_title,
                "review_plan": plan,
                "previous_answer": _clip(previous, budget // 12),
                "untrusted_evidence_json": overview,
                "task": "Ask up to three focused, unanswered questions.",
            }, overview_ids | set(seen), check)
            questions = _clip(questions, budget // 8)
            report(f"Phase 2/3: retrieving evidence and answering, round {turn}/{self.settings.rounds}…")
            retrieved, retrieved_ids = evidence_for(index.retrieve(questions + " " + safe_title, 12), budget // 2)
            # Reword removed references so specialist never sees unsupported source labels.
            grounded_questions = _CITATION.sub(lambda match: match.group() if match.group() in retrieved_ids else "[source not in this turn]", questions)
            answer = self._call(SPECIALIST_SYSTEM, {
                "paper_title": safe_title,
                "questions": grounded_questions,
                "untrusted_evidence_json": retrieved,
                "task": "Answer from these excerpts only, cite claims, and identify unanswered questions.",
            }, retrieved_ids, check)
            dialogue.append({"round": turn, "skeptic": questions, "specialist": _clip(answer, 3600)})
            previous = answer

        report("Phase 3/3: synthesizing the evidence-grounded review…")
        # Prioritize evidence used by the specialist, then add diverse source coverage.
        cited = set(_CITATION.findall(json.dumps(dialogue, ensure_ascii=False)))
        candidates = [chunk for identifier, chunk in seen.items() if identifier in cited]
        candidates += [chunk for identifier, chunk in seen.items() if identifier not in cited]
        final_evidence, final_chunks = _evidence(candidates, budget // 2)
        final_ids = {f"[{chunk.identifier}]" for chunk in final_chunks}
        dialogue_text = _clip(json.dumps(dialogue, ensure_ascii=False), budget // 5)
        dialogue_text = _CITATION.sub(lambda match: match.group() if match.group() in final_ids else "[evidence omitted]", dialogue_text)
        final = self._call(SYNTHESIS_SYSTEM, {
            "paper_title": safe_title,
            "untrusted_discussion": dialogue_text,
            "untrusted_evidence_json": final_evidence,
            "task": "Write the final review with the three required headings and exact evidence citations.",
        }, final_ids, check)
        final_citations = _validate_citations(final, final_ids, required=True)
        heading_matches = [list(re.finditer(r"^" + re.escape(heading) + r"[ \t]*$", final, re.MULTILINE)) for heading in _HEADINGS]
        positions = [matches[0].start() if len(matches) == 1 else -1 for matches in heading_matches]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            raise ReviewError("The model omitted or reordered the required review headings. Retry the review.")
        for index_no, position in enumerate(positions):
            end = positions[index_no + 1] if index_no + 1 < len(positions) else len(final)
            if not final[position + len(_HEADINGS[index_no]):end].strip():
                raise ReviewError("The model returned an empty review section. Retry the review.")

        appendix = ["", "---", "## Evidence excerpts", "", "Source identifiers refer to the extracted PDF page and text chunk, not a verified external reference.", ""]
        for chunk in final_chunks:
            if f"[{chunk.identifier}]" in final_citations:
                appendix.append(f"### [{chunk.identifier}] — PDF page {chunk.page}")
                appendix.append("")
                appendix.extend("> " + html.escape(line, quote=False) for line in chunk.text.splitlines())
                appendix.append("")
        appendix += [
            "## Review scope",
            "",
            f"STORM-inspired single-paper adaptation; {self.settings.rounds} adversarial QA round(s). "
            f"The workflow retrieved {len(seen)} of {len(chunks)} text chunks across {len(pages)} PDF pages; "
            f"{len(final_chunks)} chunks were available to final synthesis. "
            "This is an AI-generated assessment of extracted text. Citation identifiers were checked against "
            "the supplied excerpts, but citation correctness does not establish that the model's claims follow "
            "from those excerpts. No external literature search, OCR, figure interpretation, or independent "
            "verification of novelty was performed. Review the source before relying on conclusions.",
        ]
        check()
        return final + "\n" + "\n".join(appendix) + "\n"
