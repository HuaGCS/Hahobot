"""Regression: concurrent Copilot token refresh must fetch exactly once.

`_get_copilot_access_token` had a check-then-act race — two concurrent chat()
calls after expiry both exchanged a new token and clobbered each other. A lock
with double-checked locking keeps it to one fetch per expiry window. Ported from
nanobot `28011413`.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from hahobot.providers.github_copilot_provider import GitHubCopilotProvider


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"token": "tok-abc", "refresh_in": 1500}


class _FakeClient:
    def __init__(self, counter: list[int], gate: asyncio.Event) -> None:
        self._counter = counter
        self._gate = gate

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get(self, *args, **kwargs) -> _FakeResponse:
        self._counter[0] += 1
        # Hold the "network" open so all waiters pile up on the lock first.
        await self._gate.wait()
        return _FakeResponse()


async def test_concurrent_token_fetch_happens_once() -> None:
    provider = GitHubCopilotProvider()
    fetches = [0]
    gate = asyncio.Event()

    with (
        patch(
            "hahobot.providers.github_copilot_provider._load_github_token",
            return_value=SimpleNamespace(access="gh-token"),
        ),
        patch(
            "hahobot.providers.github_copilot_provider.httpx.AsyncClient",
            lambda *a, **k: _FakeClient(fetches, gate),
        ),
    ):
        tasks = [asyncio.create_task(provider._get_copilot_access_token()) for _ in range(5)]
        await asyncio.sleep(0.01)  # let all 5 reach the lock / the single fetch
        gate.set()
        results = await asyncio.gather(*tasks)

    assert fetches[0] == 1  # only one coroutine did the exchange
    assert results == ["tok-abc"] * 5
