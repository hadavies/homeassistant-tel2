"""Tests for the fake Tel2 API helper."""

from collections.abc import AsyncGenerator
from typing import Any

import aiohttp
import pytest

from custom_components.tel2.const import API_URL
from tests.fake_tel2 import FakeTel2Api
from tests.fixture_loader import load_fixture


@pytest.fixture
async def session(fake_tel2: FakeTel2Api) -> AsyncGenerator[aiohttp.ClientSession]:
    session = fake_tel2.create_session()
    yield session
    await session.close()


async def _post(session: aiohttp.ClientSession, body: dict[str, Any]) -> Any:
    async with session.post(API_URL, json=body) as response:
        return await response.json()


def _body(action: str, context: str, **params: str) -> dict[str, Any]:
    return {"token": "t", "action": action, "context": context, "params": params}


async def test_keyed_fixture_is_served_per_line(session: aiohttp.ClientSession) -> None:
    usage = load_fixture("get_line_bundle_usage.json")

    first = await _post(
        session, _body("Get-Line-Bundle-Usage", "account", line="01632960001")
    )
    unknown = await _post(
        session, _body("Get-Line-Bundle-Usage", "account", line="01632960999")
    )

    assert first == usage["01632960001"]
    assert unknown["Result"] == "0"


async def test_responses_are_served_in_order_and_the_last_repeats(
    fake_tel2: FakeTel2Api, session: aiohttp.ClientSession
) -> None:
    fake_tel2.respond(
        "Get-Webhooks", "error_generic.json", "get_webhooks_populated.json"
    )

    results = [await _post(session, _body("Get-Webhooks", "api")) for _ in range(3)]

    assert [r["Result"] for r in results] == ["-1", "1", "1"]
    assert fake_tel2.call_count("Get-Webhooks") == 3


async def test_wrong_context_fails_loudly(session: aiohttp.ClientSession) -> None:
    with pytest.raises(AssertionError, match="expected 'account'"):
        await _post(session, _body("Get-Account-Balance", "voice"))


async def test_override_still_checks_the_expected_context(
    fake_tel2: FakeTel2Api, session: aiohttp.ClientSession
) -> None:
    fake_tel2.respond("Get-Account-Balance", "error_generic.json")

    with pytest.raises(AssertionError, match="expected 'account'"):
        await _post(session, _body("Get-Account-Balance", "voice"))


async def test_override_pinned_to_a_context_skips_the_check(
    fake_tel2: FakeTel2Api, session: aiohttp.ClientSession
) -> None:
    fake_tel2.respond("Get-Account-Balance", "error_generic.json", context="voice")

    result = await _post(session, _body("Get-Account-Balance", "voice"))

    assert result == load_fixture("error_generic.json")
