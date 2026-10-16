"""Fake Tel2 API that routes POSTs on the action and context in the JSON body."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
import json
from typing import Any

from aiohttp import ClientSession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.tel2.const import API_URL
from tests.fixture_loader import FIXTURES_DIR, load_fixture

ACTION_CONTEXTS: Mapping[str, str] = {
    "Get-Customer-Details": "account",
    "Get-Account-Balance": "account",
    "Get-Current-Plan-Summary": "account",
    "Get-Line-Bundle-Usage": "account",
    "Get-Numbers": "voice",
    "Get-Line-Names": "voice",
    "Get-Subscriber-Status": "voice",
    "Get-All-Pref-Summary": "voice",
    "Get-Pref-Summary": "voice",
    "Get-Pref-Value": "voice",
    "Set-Pref-Value": "voice",
    "Enable-Recording": "voice",
    "Disable-Recording": "voice",
    "Resync-Line": "voice",
    "Reboot-Phone": "voice",
    "Get-Webhooks": "api",
    "Add-Webhook": "api",
    "Delete-Webhook": "api",
}

NO_DATA: Mapping[str, Any] = {
    "Result": "0",
    "Status": "Success",
    "Message": "No Data Found.",
    "Data": [],
}


@dataclass(frozen=True, slots=True)
class RawResponse:
    """A response body sent verbatim, for malformed or non-JSON replies."""

    body: str | bytes
    status: int = 200


type FakeResponse = str | RawResponse | BaseException | Mapping[str, Any] | list[Any]


@dataclass(frozen=True, slots=True)
class FakeRequest:
    """One request the fake received."""

    method: str
    url: URL
    headers: Mapping[str, str]
    body: dict[str, Any]

    @property
    def action(self) -> str | None:
        """Return the Tel2 action."""
        return self.body.get("action")

    @property
    def context(self) -> str | None:
        """Return the Tel2 context."""
        return self.body.get("context")

    @property
    def params(self) -> dict[str, Any]:
        """Return the request params."""
        return self.body.get("params") or {}


@cache
def _keyed_fixtures() -> Mapping[str, str]:
    index = load_fixture("index.json")
    return {
        entry["file"]: entry["keyed_by"]
        for entry in index["files"]
        if "keyed_by" in entry
    }


def default_fixture(action: str) -> str:
    """Return the fixture file named after an action, e.g. Get-Token -> get_token.json."""
    return action.lower().replace("-", "_") + ".json"


class FakeTel2Api:
    """Serve Tel2 fixtures on aioclient_mock and record every request.

    By default each action returns the fixture named after it. `respond` overrides
    an action with a fixture name, a JSON body, a RawResponse or an exception to
    raise; several responses are served in order and the last one repeats.
    """

    def __init__(self, mocker: AiohttpClientMocker, api_url: str = API_URL) -> None:
        """Register the fake on the mocker."""
        self._mocker = mocker
        self.api_url = URL(api_url)
        self.requests: list[FakeRequest] = []
        self._overrides: dict[tuple[str, str | None], list[FakeResponse]] = {}
        mocker.post(api_url, side_effect=self._handle)

    def respond(
        self, action: str, *responses: FakeResponse, context: str | None = None
    ) -> None:
        """Override the responses for an action, optionally only in one context."""
        if not responses:
            raise ValueError("respond needs at least one response")
        self._overrides[(action, context)] = list(responses)

    def requests_for(self, action: str) -> list[FakeRequest]:
        """Return the requests made for an action."""
        return [request for request in self.requests if request.action == action]

    def call_count(self, action: str) -> int:
        """Return how many times an action was requested."""
        return len(self.requests_for(action))

    def create_session(self) -> ClientSession:
        """Return a ClientSession served by this fake; the caller closes it."""
        return self._mocker.create_session(asyncio.get_running_loop())

    async def _handle(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        # The mocker appends the call just before awaiting us, so this is our entry.
        headers = self._mocker.mock_calls[-1][3] or {}
        request = FakeRequest(method, url, dict(headers), _parse_body(data))
        self.requests.append(request)
        response = self._next_response(request)
        # Yield like a real network round trip so concurrency tests are meaningful.
        await asyncio.sleep(0)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, RawResponse):
            body = response.body
            content = body.encode() if isinstance(body, str) else body
            return AiohttpClientMockResponse(
                method, url, status=response.status, response=content
            )
        if isinstance(response, str):
            response = _fixture_body(response, request)
        return AiohttpClientMockResponse(method, url, json=response)

    def _next_response(self, request: FakeRequest) -> FakeResponse:
        action = request.action
        if not isinstance(action, str):
            raise TypeError(f"Tel2 request without an action: {request.body!r}")
        pinned = self._overrides.get((action, request.context))
        if pinned:
            return _pop(pinned)
        expected = ACTION_CONTEXTS.get(action)
        if expected is not None and request.context != expected:
            raise AssertionError(
                f"{action} sent in context {request.context!r}, expected {expected!r}"
            )
        if queue := self._overrides.get((action, None)):
            return _pop(queue)
        name = default_fixture(action)
        if not (FIXTURES_DIR / name).is_file():
            raise AssertionError(f"No fixture for {action}; add one or call respond()")
        return name


def _pop(queue: list[FakeResponse]) -> FakeResponse:
    return queue.pop(0) if len(queue) > 1 else queue[0]


def _parse_body(data: Any) -> dict[str, Any]:
    if isinstance(data, bytes | str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise TypeError(f"Tel2 request body is not a JSON object: {data!r}")
    return data


def _fixture_body(name: str, request: FakeRequest) -> Any:
    body = load_fixture(name)
    key = _keyed_fixtures().get(name)
    if key is None:
        return body
    return body.get(request.params.get(key), NO_DATA)
