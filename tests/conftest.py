"""Shared pytest configuration for Faramir.

- Puts src/ on the import path (existing tests also do this themselves).
- Blocks real network access for every test unless it is marked
  ``allow_network``. Tests must use the fakes in tests/fakes instead.
- Registers the ``local_capture`` marker for opt-in tests that read source
  pages captured during the audit; those files are never committed, so the
  tests are skipped unless ``--run-local-capture`` is passed.
"""

import os
import socket
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
for _p in (SRC, ROOT):  # ROOT makes ``tests.fakes`` importable
    if _p not in sys.path:
        sys.path.insert(0, _p)


def pytest_addoption(parser):
    parser.addoption(
        "--run-local-capture",
        action="store_true",
        default=False,
        help="run tests that need locally captured (uncommitted) source pages",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_network: test may open real network connections")
    config.addinivalue_line("markers", "no_run_context: run without the default permissive test run context")
    config.addinivalue_line("markers", "local_capture: needs uncommitted captured source pages")
    config.addinivalue_line(
        "markers",
        "baseline: pins current (pre-v2) behavior; a later increment may change it deliberately",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-local-capture"):
        return
    skip = pytest.mark.skip(reason="needs --run-local-capture")
    for item in items:
        if "local_capture" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _test_run_context(request, tmp_path):
    """Unit tests of write functions run inside a permissive live context whose
    writes only ever reach the in-memory fakes. Tests of the guard itself mark
    themselves ``no_run_context`` and install their own context."""
    from faramir import runtime

    if "no_run_context" in request.keywords:
        with runtime.use(None):
            yield None
        return
    ctx = runtime.RunContext(mode=runtime.LIVE, enabled=True, run_id="test", out_dir=str(tmp_path / "out"))
    with runtime.use(ctx):
        yield ctx


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    if "allow_network" in request.keywords:
        yield
        return

    def _refuse(*args, **kwargs):
        raise NetworkBlocked("real network access is disabled in tests; use tests/fakes")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield
