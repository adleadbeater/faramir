"""Fake Anthropic client returning scripted responses.

Install with ``FakeAnthropic([...]).install(monkeypatch)``; it replaces
``anthropic.Anthropic`` so ``faramir.claude_select`` builds the fake instead of
a real client. Each scripted item is response text (str), a FakeMessage, an
Exception to raise from ``messages.create``, or a callable taking the request
kwargs and returning one of those.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import anthropic


@dataclass
class FakeUsage:
    input_tokens: int = 1000
    output_tokens: int = 200
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeMessage:
    content: list
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_details: object = None


class _Messages:
    def __init__(self, owner: "FakeAnthropic"):
        self._owner = owner

    def count_tokens(self, **kwargs):
        self._owner.count_calls.append(kwargs)
        if self._owner.token_counts:
            item = self._owner.token_counts.pop(0)
            if isinstance(item, Exception):
                raise item
            return _Count(item)
        return _Count(len(kwargs["messages"][0]["content"]) // 4 + 2000)

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        if not self._owner.script:
            raise AssertionError("FakeAnthropic: no scripted response left")
        item = self._owner.script.pop(0)
        if callable(item) and not isinstance(item, (FakeMessage, Exception)):
            item = item(kwargs)  # scripted function of the request
        if isinstance(item, Exception):
            raise item
        if isinstance(item, FakeMessage):
            return item
        return FakeMessage(content=[FakeTextBlock(item)])


class _Count:
    def __init__(self, n):
        self.input_tokens = n


class FakeAnthropic:
    def __init__(self, script: list | None = None, token_counts: list | None = None):
        self.script = list(script or [])
        self.calls: list[dict] = []
        self.count_calls: list[dict] = []
        self.token_counts = list(token_counts or [])  # scripted count_tokens results (int or Exception)
        self.client_kwargs: list[dict] = []
        self.messages = _Messages(self)

    def __call__(self, *args, **kwargs):
        # Acts as the class: anthropic.Anthropic(...) returns this instance.
        self.client_kwargs.append(kwargs)
        return self

    def install(self, monkeypatch) -> "FakeAnthropic":
        monkeypatch.setattr(anthropic, "Anthropic", self)
        return self
