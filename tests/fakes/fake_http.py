"""Routing stand-in for ``requests.get`` / ``requests.post``.

Install with ``FakeHTTP().install(monkeypatch)``. Requests are matched against
registered (method, URL-prefix) routes in registration order; an unmatched
request raises ``UnexpectedRequest`` so no test can silently reach a real host.
"""

from __future__ import annotations

import json as _json
from typing import Callable

import requests


class UnexpectedRequest(AssertionError):
    pass


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data=None, text: str | None = None, url: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.text = text if text is not None else (_json.dumps(json_data) if json_data is not None else "")
        self.url = url

    def json(self):
        if self._json is None:
            return _json.loads(self.text)
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} for {self.url}")
            err.response = self
            raise err


Handler = Callable[..., FakeResponse]


class FakeHTTP:
    def __init__(self):
        self.calls: list[dict] = []
        self._routes: list[tuple[str, str, Handler]] = []

    def route(self, method: str, prefix: str, handler: Handler | FakeResponse) -> "FakeHTTP":
        if isinstance(handler, FakeResponse):
            fixed = handler
            handler = lambda **_: fixed  # noqa: E731
        self._routes.append((method.upper(), prefix, handler))
        return self

    def _dispatch(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        for m, prefix, handler in self._routes:
            if m == method and url.startswith(prefix):
                resp = handler(url=url, **kwargs)
                resp.url = resp.url or url
                return resp
        raise UnexpectedRequest(f"no fake route for {method} {url}")

    def get(self, url, params=None, **kwargs):
        return self._dispatch("GET", url, params=params, **kwargs)

    def post(self, url, json=None, data=None, **kwargs):
        return self._dispatch("POST", url, json=json, data=data, **kwargs)

    def install(self, monkeypatch) -> "FakeHTTP":
        monkeypatch.setattr(requests, "get", self.get)
        monkeypatch.setattr(requests, "post", self.post)
        return self

    def calls_to(self, prefix: str) -> list[dict]:
        return [c for c in self.calls if c["url"].startswith(prefix)]
