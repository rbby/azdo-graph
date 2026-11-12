from __future__ import annotations

import json
from pathlib import Path

import pytest

from ado_pipeline_graph.ado import AdoClient

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    """Minimal stand-in for the object returned by urllib openers."""

    def __init__(self, value, headers=None, status=200):
        self.value = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.value


@pytest.fixture
def fixtures_dir():
    return FIXTURES


@pytest.fixture
def Response():
    return FakeResponse


@pytest.fixture
def ado_env(monkeypatch):
    monkeypatch.setenv("ADO_ORG_URL", "https://dev.azure.com/org")
    monkeypatch.setenv("ADO_PAT", "secret")


@pytest.fixture
def make_client(ado_env):
    """Client whose opener replays `steps` (a response, or an exception to raise)."""

    def factory(*steps, **kwargs):
        requests, sleeps, queue = [], [], list(steps)

        def opener(request):
            requests.append(request)
            step = queue.pop(0)
            if isinstance(step, BaseException):
                raise step
            return step

        return AdoClient(opener=opener, sleep=sleeps.append, **kwargs), requests, sleeps

    return factory
