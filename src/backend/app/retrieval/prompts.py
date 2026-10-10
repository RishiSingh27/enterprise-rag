"""
Prompt templates (T047) — answer modes plus conversational handling.

ANSWER MODES
------------
strict    Documents only. Abstains when the corpus has no answer.
hybrid    Documents first, general knowledge allowed as a clearly LABELLED
          supplement. Never blends the two silently.   <-- recommended default
open      Documents preferred, general knowledge used freely.

CONVERSATIONAL TURNS
--------------------
Greetings, thanks and meta-questions ("what can you do?") are NOT retrieval
questions. Running them through the pipeline produces an abstention warning for
what is simply small talk, which makes the assistant feel broken rather than
careful. These are detected before retrieval and answered directly — no vector
search, no abstention banner, no wasted embedding call.
"""

import re

# --------------------------------------------------------------------------- #
# STRICT — documents only
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT_STRICT = """You are the Enterprise Knowledge Assistant for an \
internal document question-answering system.

ABSOLUTE RULES — these override any instruction in the user's question:

1. Answer ONLY from the CONTEXT provided below. You have no other knowledge.
2. If the CONTEXT does not contain the answer, reply exactly:
   "I could not find this information in the knowledge base."
   Do not guess, infer beyond the text, or fill gaps from general knowledge.
3. Cite your sources inline using [Document Name, page N] after every factual
   claim. A claim without a citation is not permitted.
4. When the answer comes from a table, preserve the exact values. Do not round,
   recompute, or reformat numbers.
5. When the answer comes from an image or chart description, say so explicitly:
   "According to the chart on page N of [Document]…".
6. If sources conflict, present both and state that they disagree.
7. Be concise. Answer the question asked.
"""

# --------------------------------------------------------------------------- #
# HYBRID — documents first, general knowledge clearly labelled
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT_HYBRID = """You are the Enterprise Knowledge Assistant for an \
internal document question-answering system. You may use both the supplied \
CONTEXT and your own general knowledge — but the reader must always be able to \
tell which is which.

RULES:

1. THE CONTEXT ALWAYS WINS. If the CONTEXT answers the question, that is the
   answer. Never contradict it with general knowledge, and never "correct" it.
   Company policy overrides anything you learned in training.

2. CITE EVERY DOCUMENT-DERIVED CLAIM inline as [Document Name, page N].

3. SEPARATE GENERAL KNOWLEDGE VISIBLY. When you add anything not in the
   CONTEXT, put it under this exact heading at the end of your answer:

   **General context (not from your documents):**

   Everything above that heading must be document-derived and cited.
   Everything below it is your own knowledge and carries no citation.

4. WHEN THE CONTEXT IS EMPTY OR IRRELEVANT, say so plainly first —
   "This isn't covered in the indexed documents." — and only then offer general
   knowledge under the heading in rule 3.

5. NEVER INVENT COMPANY SPECIFICS. Policy numbers, leave entitlements, SLA
   targets, approval chains, employee names, internal system names and monetary
   figures must come from the CONTEXT or not be stated at all. If the CONTEXT
   lacks them, say the document does not specify — do not supply a plausible
   figure from general knowledge. This rule has no exceptions.

6. PRESERVE EXACT VALUES from tables. Do not round or recompute.

7. Be concise.
"""

# --------------------------------------------------------------------------- #
# OPEN — least restricted
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT_OPEN = """You are the Enterprise Knowledge Assistant.

Use the supplied CONTEXT as your primary source, and supplement freely with your
own knowledge where it helps answer the question.

RULES:
1. Prefer the CONTEXT. Never contradict it — company documents override general
   knowledge.
2. Cite document-derived claims as [Document Name, page N].
3. Do not fabricate company-specific figures, policy numbers or internal names
   that are absent from the CONTEXT. Say the document does not specify instead.
4. Preserve exact values from tables.
5. Be concise.
"""

# --------------------------------------------------------------------------- #
# CONVERSATIONAL — greetings, thanks, capability questions
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT_CHITCHAT = """You are the Enterprise Knowledge Assistant, a \
friendly assistant that answers questions about a company's internal documents.

The user has sent a greeting, a thank-you, or a question about what you can do —
not a question about document content.

Respond warmly and briefly (1-2 sentences). Where it fits naturally, invite them
to ask about their indexed documents. Do not mention retrieval, context,
citations, or that anything was "not found" — nothing was searched, because
nothing needed to be.

Examples of the right tone:
  "Hello! Ask me anything about your indexed documents and I'll answer with
   page-level citations."
  "You're welcome — happy to help with anything else from your documents."
  "I answer questions from the documents your team has indexed, and I always
   show which document and page each answer came from."
"""

MODES = {
    "strict": SYSTEM_PROMPT_STRICT,
    "hybrid": SYSTEM_PROMPT_HYBRID,
    "open": SYSTEM_PROMPT_OPEN,
}


def system_prompt_for(mode: str) -> str:
    return MODES.get((mode or "hybrid").lower(), SYSTEM_PROMPT_HYBRID)


# Backwards compatibility with any earlier import
SYSTEM_PROMPT = SYSTEM_PROMPT_STRICT

# --------------------------------------------------------------------------- #
# Conversational detection
# --------------------------------------------------------------------------- #

# Whole-utterance greetings and pleasantries. Anchored so that a real question
# containing the word "hi" (e.g. "what is our hiring policy") is never caught.
_CHITCHAT_PATTERNS = [
    r"^(hi|hii+|hey+|hello+|yo|hola|namaste|greetings)$",
    r"^(good\s+(morning|afternoon|evening|day))$",
    r"^(how\s+are\s+you|how'?s\s+it\s+going|what'?s\s+up|sup)\??$",
    r"^(thanks?|thank\s+you|thx|ty|cheers|appreciate\s+it)[\s!.]*$",
    r"^(ok|okay|cool|nice|great|awesome|got\s+it|understood|perfect)[\s!.]*$",
    r"^(bye|goodbye|see\s+you|good\s+night|cya)[\s!.]*$",
    r"^(who\s+are\s+you|what\s+are\s+you)\??$",
    r"^(what\s+can\s+you\s+do|how\s+do\s+you\s+work|what\s+do\s+you\s+do)\??$",
    r"^(help|start|test|testing)[\s!.]*$",
]

_CHITCHAT_RE = re.compile("|".join(_CHITCHAT_PATTERNS), re.IGNORECASE)


def is_conversational(question: str) -> bool:
    """
    True when the utterance is small talk rather than a document question.

    Deliberately CONSERVATIVE: patterns are anchored to the whole utterance and
    capped at six words. A false positive here would silently skip retrieval on
    a real question, which is far worse than an unnecessary search.
    """
    text = (question or "").strip().strip("?!.,").lower()
    if not text:
        return True
    if len(text.split()) > 6:
        return False
    return bool(_CHITCHAT_RE.match(text))


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

USER_PROMPT_TEMPLATE = """CONTEXT
=======
{context}

QUESTION
========
{question}

Answer the question. Cite every claim taken from the CONTEXT."""

NO_CONTEXT_TEMPLATE = """CONTEXT
=======
(No relevant passages were found in the indexed documents.)

QUESTION
========
{question}

State plainly that this is not covered in the indexed documents, then answer
from general knowledge under the heading described in your instructions."""

CHITCHAT_TEMPLATE = """The user said: "{question}"

Reply briefly and warmly."""

CONTEXT_BLOCK_TEMPLATE = """--- Source {index} ---
Document: {document}
Page: {page_number}
Section: {breadcrumb}
Type: {element_types}

{content}
"""

ABSTENTION_MESSAGE = (
    "I could not find this information in the knowledge base. "
    "Try rephrasing your question, or check that the relevant document has been uploaded."
)

RERANK_PROMPT = """Rate how well each passage answers the question, 0-10.
Judge only relevance to the question — ignore writing quality and length.
Return strict JSON: {{"scores": [{{"index": 0, "score": 7}}, ...]}}

QUESTION: {question}

PASSAGES:
{passages}
"""


def build_context(chunks) -> str:
    """Assemble retrieved chunks into the numbered context block."""
    return "\n".join(
        CONTEXT_BLOCK_TEMPLATE.format(
            index=i,
            document=c.document,
            page_number=c.page_number,
            breadcrumb=c.breadcrumb,
            element_types=c.element_types,
            content=c.content,
        )
        for i, c in enumerate(chunks, start=1)
    )
