"""
Grounded answer generation (T048, T049) and LLM reranking (T045).

ANSWER MODES (set ANSWER_MODE in .env)
--------------------------------------
strict  documents only; abstains when nothing relevant is retrieved
hybrid  documents first, general knowledge appended under a visible heading
open    documents preferred, general knowledge used freely

Conversational turns (greetings, thanks, "what can you do?") never reach the
retrieval pipeline at all — routes_query.py detects them and calls chitchat()
directly. That keeps small talk warm and instant instead of producing an
abstention warning for a "hi".
"""

from __future__ import annotations

import json
import logging

from .prompts import (
    ABSTENTION_MESSAGE,
    CHITCHAT_TEMPLATE,
    NO_CONTEXT_TEMPLATE,
    RERANK_PROMPT,
    SYSTEM_PROMPT_CHITCHAT,
    USER_PROMPT_TEMPLATE,
    build_context,
    system_prompt_for,
)

log = logging.getLogger("ekb.generator")


class Reranker:
    """
    T045 / FR-023. Fusion produces good candidates; reranking orders them by
    actual relevance before we spend context tokens on them.
    """

    def __init__(self, settings):
        self.settings = settings

    def rerank(self, question: str, chunks, top_k: int):
        if len(chunks) <= top_k:
            return chunks[:top_k]
        try:
            from ..core.azure_clients import get_openai_client

            passages = "\n\n".join(f"[{i}] {c.content[:600]}" for i, c in enumerate(chunks))
            response = get_openai_client().chat.completions.create(
                model=self.settings.azure_chat_deployment,
                messages=[{
                    "role": "user",
                    "content": RERANK_PROMPT.format(question=question, passages=passages),
                }],
                temperature=0,
                max_tokens=600,
                response_format={"type": "json_object"},
            )
            scores = json.loads(response.choices[0].message.content or "{}").get("scores", [])
            ranking = {int(s["index"]): float(s["score"]) for s in scores}
            ordered = sorted(range(len(chunks)), key=lambda i: ranking.get(i, 0.0), reverse=True)
            return [chunks[i] for i in ordered[:top_k]]
        except Exception as exc:  # noqa: BLE001
            # Reranking is an optimisation, never a hard dependency.
            log.warning("rerank.failed_using_fusion_order", extra={"error": str(exc)[:200]})
            return chunks[:top_k]


class Generator:
    def __init__(self, settings):
        self.settings = settings

    @property
    def mode(self) -> str:
        return (getattr(self.settings, "answer_mode", "hybrid") or "hybrid").lower()

    # ------------------------------------------------------------------ #
    # Conversational
    # ------------------------------------------------------------------ #

    def chitchat(self, question: str) -> tuple[str, dict]:
        """
        Answer a greeting or meta-question directly — no retrieval, no context,
        no citation machinery. Temperature is higher here because the goal is a
        natural reply, not an extractive one.
        """
        try:
            from ..core.azure_clients import get_openai_client

            response = get_openai_client().chat.completions.create(
                model=self.settings.azure_chat_deployment,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_CHITCHAT},
                    {"role": "user", "content": CHITCHAT_TEMPLATE.format(question=question)},
                ],
                temperature=0.7,
                max_tokens=150,
            )
            answer = (response.choices[0].message.content or "").strip()
            usage = {
                "prompt": getattr(response.usage, "prompt_tokens", 0),
                "completion": getattr(response.usage, "completion_tokens", 0),
            }
            return answer, usage
        except Exception as exc:  # noqa: BLE001
            log.warning("chitchat.failed", extra={"error": str(exc)[:200]})
            return (
                "Hello! Ask me anything about your indexed documents and I'll "
                "answer with page-level citations.",
                {"prompt": 0, "completion": 0},
            )

    # ------------------------------------------------------------------ #
    # Document answering
    # ------------------------------------------------------------------ #

    def _build_messages(self, question: str, retrieval):
        """Returns the message list, or None when we should abstain outright."""
        mode = self.mode
        has_context = bool(retrieval.chunks) and not retrieval.abstained

        if not has_context and mode == "strict":
            return None          # FR-025: do not call the LLM at all

        system = system_prompt_for(mode)

        if has_context:
            user = USER_PROMPT_TEMPLATE.format(
                context=build_context(retrieval.chunks), question=question
            )
        else:
            # Nothing relevant retrieved, but the mode permits general knowledge.
            user = NO_CONTEXT_TEMPLATE.format(question=question)

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _temperature(self) -> float:
        """
        Strict mode stays cold because the task is extractive. The looser modes
        get slightly more headroom so general-knowledge passages read naturally,
        but never high enough to encourage invention.
        """
        base = self.settings.llm_temperature
        return base if self.mode == "strict" else max(base, 0.3)

    def generate(self, question: str, retrieval) -> tuple[str, dict]:
        """Returns ``(answer, token_usage)``."""
        messages = self._build_messages(question, retrieval)
        if messages is None:
            return ABSTENTION_MESSAGE, {"prompt": 0, "completion": 0}

        from ..core.azure_clients import get_openai_client

        response = get_openai_client().chat.completions.create(
            model=self.settings.azure_chat_deployment,
            messages=messages,
            temperature=self._temperature(),
            max_tokens=self.settings.llm_max_tokens,
        )
        answer = (response.choices[0].message.content or "").strip()
        usage = {
            "prompt": getattr(response.usage, "prompt_tokens", 0),
            "completion": getattr(response.usage, "completion_tokens", 0),
        }
        return answer, usage

    def stream(self, question: str, retrieval):
        """T049 / FR-027. Yields answer tokens as they arrive."""
        messages = self._build_messages(question, retrieval)
        if messages is None:
            yield ABSTENTION_MESSAGE
            return

        from ..core.azure_clients import get_openai_client

        stream = get_openai_client().chat.completions.create(
            model=self.settings.azure_chat_deployment,
            messages=messages,
            temperature=self._temperature(),
            max_tokens=self.settings.llm_max_tokens,
            stream=True,
        )
        for part in stream:
            if part.choices and part.choices[0].delta.content:
                yield part.choices[0].delta.content
