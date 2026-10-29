"""Agent loop, scope guard, and grounding checks. No network, no database.

Run from the server directory: uv run pytest
"""

import pytest

from llm import agent
from llm.tool_defs import ToolResult

FORWARD_LOOKING = [
    "What is the company's forward guidance for next quarter?",
    "What is NVDA's revenue forecast for next year?",
    "What price target do analysts have on AAPL?",
    "Will they beat earnings next quarter?",
    "What is NVDA's forward P/E?",
    "What is the forward EPS for MSFT?",
    "What are projected revenues for next year?",
    "What's the upside from here?",
]

ANSWERABLE = [
    "What were NVDA's revenue and net income for the last three fiscal years?",
    "Which company had the highest gross margin last year?",
    "What is AAPL's trailing P/E right now?",
    "What new risk factors did NVDA add in its latest 10-K?",
    "How did MSFT's revenue grow last year, and what management attributed it to?",
    # Mentions "forward" but asks about filing text, so it must not be refused.
    "What forward-looking statements do they disclose in Item 1A?",
]


class FakeChat:
    """Replays scripted replies and records what it was sent."""

    def __init__(self, responses):
        self.remaining = list(responses)
        self.calls = []

    def __call__(self, messages, tools):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.remaining.pop(0)


def tool_call(name: str, arguments: str = "{}") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}],
    }


def answer(text: str) -> dict:
    return {"role": "assistant", "content": text}


def valuation_tool(**_: object) -> ToolResult:
    return ToolResult(
        content={"ticker": "AAPL", "trailing_pe": 46.23, "fiscal_year": 2025},
        citations=[{"ticker": "AAPL", "period_end": "2025-09-27", "accession": "0000320193-25-000079"}],
    )


# --- scope guard ------------------------------------------------------------


@pytest.mark.parametrize("question", FORWARD_LOOKING)
def test_forward_looking_questions_are_refused(question):
    assert agent.check_scope(question) is not None


@pytest.mark.parametrize("question", ANSWERABLE)
def test_questions_about_reported_data_pass_through(question):
    assert agent.check_scope(question) is None


def test_refusal_does_not_call_the_model():
    chat = FakeChat([])

    run = agent.run_agent("What is the guidance for next quarter?", chat, {}, "prompt")

    assert run.refused is True
    assert chat.calls == []
    assert run.tools_used == []


# --- grounding --------------------------------------------------------------

PAYLOADS = [{"ticker": "AAPL", "trailing_pe": 46.23, "fiscal_year": 2025}]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("AAPL trades at 46.23x FY2025 earnings.", []),
        ("AAPL trades at 46.2x earnings.", []),  # rounding within 1%
        ("AAPL trades at 52.4x earnings.", ["52.4"]),
        ("The 10-K reports 46.23x.", []),  # form names are not figures
    ],
)
def test_unverified_numbers(text, expected):
    assert agent.find_unverified_numbers(text, PAYLOADS) == expected


def test_a_ratio_written_as_a_percentage_is_verified():
    payloads = [{"ticker": "NVDA", "gross_margin": 0.7107}]
    assert agent.find_unverified_numbers("NVDA gross margin was 71.07%.", payloads) == []


def test_a_figure_written_in_billions_is_verified():
    payloads = [{"revenue": 215_938_000_000}]
    assert agent.find_unverified_numbers("Revenue was $215.9 billion.", payloads) == []


# --- http client ------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_chat_retries_after_a_rate_limit(monkeypatch):
    import httpx

    responses = [
        _FakeResponse(429),
        _FakeResponse(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}),
    ]
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)

    chat = agent.openai_chat("https://example.test/v1", "key", "model")

    assert chat([], [])["content"] == "ok"


def test_chat_gives_up_after_repeated_rate_limits(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _FakeResponse(429))
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)

    chat = agent.openai_chat("https://example.test/v1", "key", "model")

    with pytest.raises(agent.ChatError):
        chat([], [])


# --- tool loop --------------------------------------------------------------


def test_tool_call_then_answer():
    chat = FakeChat([tool_call("get_valuation", '{"ticker": "AAPL"}'), answer("AAPL trades at 46.23x earnings.")])

    run = agent.run_agent("What is AAPL's trailing P/E?", chat, {"get_valuation": valuation_tool}, "prompt")

    assert run.tools_used == ["get_valuation"]
    assert run.citations[0]["ticker"] == "AAPL"
    assert run.unverified_numbers == []
    assert run.refused is False

    tool_messages = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert "46.23" in tool_messages[0]["content"]


def test_answer_without_a_tool_still_returns():
    chat = FakeChat([answer("I would need to look that up.")])

    run = agent.run_agent("Hello", chat, {}, "prompt")

    assert run.answer == "I would need to look that up."
    assert run.tools_used == []


def test_unknown_tool_is_reported_to_the_model():
    chat = FakeChat([tool_call("no_such_tool"), answer("That data is not available.")])

    run = agent.run_agent("Something", chat, {}, "prompt")

    assert run.tools_used == []
    tool_message = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "unknown tool" in tool_message["content"]


def test_invalid_arguments_are_reported_not_raised():
    chat = FakeChat([tool_call("get_valuation", "{not json"), answer("I could not retrieve that.")])

    run = agent.run_agent("P/E?", chat, {"get_valuation": valuation_tool}, "prompt")

    assert run.tools_used == []
    tool_message = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "error" in tool_message["content"]


def test_step_budget_is_enforced():
    chat = FakeChat([tool_call("get_valuation", '{"ticker": "AAPL"}') for _ in range(10)])

    run = agent.run_agent("Loop forever", chat, {"get_valuation": valuation_tool}, "prompt", max_steps=2)

    assert "could not finish" in run.answer
    assert run.tools_used == ["get_valuation", "get_valuation"]


def test_spelled_dates_and_item_references_are_ignored():
    payloads = [{"price_date": "2026-10-28", "trailing_pe": 46.09}]
    answer = "As of October 28, 2026, the trailing P/E (Item 7) was 46.09."
    assert agent.find_unverified_numbers(answer, payloads) == []
