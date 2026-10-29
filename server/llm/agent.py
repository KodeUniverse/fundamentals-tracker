"""The agent loop: scope guard, tool-calling turns, and a grounding check.

The model does three things and nothing else: decide which tool answers the
question, read what the tool returned, and phrase the answer. Every number it
reports comes from a tool result, which is what the grounding check verifies.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from llm.tool_defs import TOOL_SPECS, ToolError, ToolResult

MAX_STEPS = 5

# Free tiers rate-limit by the minute and a question costs two or three calls, so
# a short backoff is cheaper than failing the request outright.
RETRYABLE_STATUS = {429, 503}
MAX_ATTEMPTS = 3

REFUSAL = (
    "I can only answer from reported fundamentals and the text of the latest 10-K filings. "
    "Forward-looking questions such as guidance, forecasts, or price targets are out of scope."
)

# A deterministic guard, not a model decision. Models are agreeable and will
# produce a plausible guidance number rather than admit they cannot know one.
#
# "forward" is scoped to a metric on purpose: bare "forward" would refuse
# legitimate filing questions such as "what forward-looking statements do they
# disclose in Item 1A".
_FORWARD_LOOKING = re.compile(
    r"\b("
    r"guidance|outlook|forecast\w*|projection\w*|projected|"
    r"forward\s+(?:p/?e|eps|earnings|revenue|sales|multiple|estimate\w*)|"
    r"price target|target price|consensus|analyst estimates?|"
    r"next (?:quarter|year|fiscal year|fy)|upcoming|"
    r"expect(?:s|ed)? to|will (?:they|it|the company)|going forward|"
    r"upside|downside|"
    r"future (?:revenue|growth|earnings|results)"
    r")\b",
    re.IGNORECASE,
)

_FORM_NAMES = re.compile(r"\b10-[KQ](?:/A)?\b", re.IGNORECASE)
_ITEM_REF = re.compile(r"\bItem\s+\d+[A-Za-z]?\b", re.IGNORECASE)
_SPELLED_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")

_SYSTEM = """You answer questions about these companies using only the tools provided:
{universe}

Rules:
- Call a tool before stating any company-specific fact. If no tool can answer, say the data is not available.
- Never calculate or estimate. The tools return revenue, margins, growth, multiples and rankings already
  computed; report them as given.
- When a tool returns null or says data is missing, say so plainly. Do not substitute a plausible number.
- When you quote filing text, name the ticker, the section, and the fiscal period.
- Answer from reported, trailing data only.
- Be brief: a few sentences, leading with the numbers that answer the question."""


class ChatError(RuntimeError):
    """The model could not be reached or returned an unusable response."""


@dataclass
class AgentRun:
    answer: str
    refused: bool = False
    tools_used: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)


def system_prompt(entities: Sequence[dict[str, Any]]) -> str:
    """System prompt listing the tracked universe, read from the database."""
    universe = ", ".join(f"{e['ticker']} ({e['entity_name']})" for e in entities if e["ticker"])
    return _SYSTEM.format(universe=universe)


def check_scope(question: str) -> str | None:
    """Refusal text when the question asks for forward-looking information."""
    return REFUSAL if _FORWARD_LOOKING.search(question) else None


def run_agent(
    question: str,
    chat: Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]],
    tool_functions: dict[str, Callable[..., ToolResult]],
    prompt: str,
    max_steps: int = MAX_STEPS,
) -> AgentRun:
    """Run the tool loop until the model answers or the step budget runs out."""
    refusal = check_scope(question)
    if refusal is not None:
        return AgentRun(answer=refusal, refused=True)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": question},
    ]
    tools_used: list[str] = []
    citations: list[dict[str, Any]] = []
    payloads: list[dict[str, Any]] = []

    for _ in range(max_steps):
        message = chat(messages, TOOL_SPECS)
        calls = message.get("tool_calls") or []

        if not calls:
            answer = (message.get("content") or "").strip()
            return AgentRun(
                answer=answer,
                tools_used=tools_used,
                citations=_unique(citations),
                unverified_numbers=find_unverified_numbers(answer, payloads),
            )

        messages.append(
            {"role": "assistant", "content": message.get("content"), "tool_calls": calls}
        )

        for call in calls:
            name = call.get("function", {}).get("name", "")
            content, citations_from_call, payload = _execute(name, call, tool_functions)
            if payload is not None:
                tools_used.append(name)
                citations.extend(citations_from_call)
                payloads.append(payload)
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id"), "content": content}
            )

    return AgentRun(
        answer="I could not finish answering within the tool budget. Try asking for one thing at a time.",
        tools_used=tools_used,
        citations=_unique(citations),
    )


def find_unverified_numbers(
    answer: str,
    payloads: Sequence[dict[str, Any]],
    tolerance: float = 0.01,
) -> list[str]:
    """Numbers in the answer that no tool returned, allowing 1% for rounding.

    It catches invented or mistyped figures, which is
    the failure that matters when the output is read as fact.
    """
    cleaned = _FORM_NAMES.sub(" ", answer)
    cleaned = _ITEM_REF.sub(" ", cleaned)
    # Spelled dates would otherwise contribute day/year fragments that appear in
    # the payload only inside ISO strings.
    cleaned = _SPELLED_DATE.sub(" ", cleaned)
    tool_numbers = [
        float(match.replace(",", ""))
        for match in _NUMBER.findall(json.dumps(payloads, default=str))
    ]
    if not tool_numbers:
        return _unique(_NUMBER.findall(cleaned))

    # A ratio stored as 0.7107 and written as "71.07%", or a figure stored in
    # dollars and written as "215.9 billion", is the same fact in another unit.
    # Accept the common scales rather than flagging correct formatting.
    scales = (1, 100, 1e-9, 1e-6, 1e-3, 1e3, 1e6, 1e9)
    candidates: list[float] = []
    for number in tool_numbers:
        candidates.extend(number * scale for scale in scales)

    unverified: list[str] = []
    for raw in _NUMBER.findall(cleaned):
        value = float(raw.replace(",", ""))
        if not any(_within(value, candidate, tolerance) for candidate in candidates):
            unverified.append(raw)
    return _unique(unverified)


def openai_chat(
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 60.0,
) -> Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]:
    """A chat function for any OpenAI-compatible `/chat/completions` endpoint."""
    # Imported here so the tool loop stays testable without the HTTP client.
    import httpx

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        delay = 2.0
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = httpx.post(
                    url,
                    headers=headers,
                    json={
                        "model": model,
                        "messages": messages,
                        "tools": tools,
                        "tool_choice": "auto",
                    },
                    timeout=timeout,
                )
            except httpx.HTTPError as exc:
                raise ChatError(f"LLM request failed: {exc}") from exc

            if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS - 1:
                wait = _retry_delay(response) or delay
                time.sleep(wait)
                delay *= 2
                continue
            break

        try:
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise ChatError(f"LLM request failed: {exc}") from exc
        except ValueError as exc:
            raise ChatError("LLM returned a response that is not JSON") from exc

        try:
            return payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatError("LLM response did not contain a message") from exc

    return chat


def _retry_delay(response: Any) -> float | None:
    """The provider's Retry-After hint, when it sends one."""
    header = response.headers.get("Retry-After") if response.headers else None
    try:
        return float(header) if header is not None else None
    except ValueError:
        return None


def _execute(
    name: str,
    call: dict[str, Any],
    tool_functions: dict[str, Callable[..., ToolResult]],
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """Run one tool call, turning failures into text the model can react to."""
    function = tool_functions.get(name)
    if function is None:
        return json.dumps({"error": f"unknown tool {name}"}), [], None

    raw_arguments = call.get("function", {}).get("arguments") or "{}"
    try:
        arguments = json.loads(raw_arguments)
        result = function(**arguments)
    except (json.JSONDecodeError, TypeError, ToolError) as exc:
        return json.dumps({"error": str(exc)}), [], None

    return json.dumps(result.content, default=str), result.citations, result.content


def _within(left: float, right: float, tolerance: float) -> bool:
    """Compare magnitudes: an ISO date in the payload would otherwise read as a negative."""
    left, right = abs(left), abs(right)
    if left == right:
        return True
    return abs(left - right) <= tolerance * max(left, right, 1.0)


def _unique(values: Sequence[Any]) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen
