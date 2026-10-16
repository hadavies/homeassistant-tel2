"""Client for the Tel2 portal API."""

import asyncio
from collections.abc import Callable
import json
import logging
from typing import Any, Final

import aiohttp
from yarl import URL

from .const import API_URL, AUTH_ERROR_PATTERNS
from .models import AccessContext

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT: Final = aiohttp.ClientTimeout(total=30)
_HEADERS: Final = {"Content-Type": "application/json", "Accept": "application/json"}
_GET_TOKEN: Final = "Get-Token"  # noqa: S105


class Tel2Error(Exception):
    """Base class for Tel2 client errors."""


class Tel2ConnectionError(Tel2Error):
    """Tel2 could not be reached or sent an unreadable response."""


class Tel2AuthError(Tel2Error):
    """Tel2 rejected the login, password or token."""


class Tel2ApiError(Tel2Error):
    """Tel2 refused a request for a reason other than authentication."""

    def __init__(
        self, message: str, context: AccessContext | None, action: str
    ) -> None:
        """Store Tel2's message with the request it answered."""
        super().__init__(message)
        self.message = message
        self.context = context
        self.action = action


class Tel2NoData(Tel2Error):  # noqa: N818
    """Tel2 answered with no data."""


def is_auth_message(message: str) -> bool:
    """Return whether a Tel2 error message means the login or token was refused."""
    lowered = message.lower()
    return any(pattern in lowered for pattern in AUTH_ERROR_PATTERNS)


class Tel2Client:
    """Talk to the Tel2 API over an injected aiohttp session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        login: str,
        password: str,
        token: str | None = None,
        on_token_refreshed: Callable[[str], None] | None = None,
        *,
        api_url: str = API_URL,
    ) -> None:
        """Initialise the client."""
        if not login:
            raise ValueError("Tel2 login is required")
        if not password:
            raise ValueError("Tel2 password is required")
        if URL(api_url).scheme != "https":
            raise ValueError("Tel2 API URL must use https")
        self._session = session
        self._login = login
        self._password = password
        self._token = token
        self._on_token_refreshed = on_token_refreshed
        self._api_url = api_url
        self._token_lock = asyncio.Lock()

    async def async_authenticate(self) -> str:
        """Fetch a new session token and return it."""
        async with self._token_lock:
            return await self._fetch_token()

    async def _call(
        self,
        context: AccessContext,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Tel2 action, renewing the token once if Tel2 refuses it."""
        token = await self._current_token()
        try:
            return await self._send(context, action, params, token)
        except Tel2AuthError:
            token = await self._renew_token(token)
        return await self._send(context, action, params, token)

    async def _call_optional(
        self,
        context: AccessContext,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Tel2 action, returning None when Tel2 has no data."""
        try:
            return await self._call(context, action, params)
        except Tel2NoData:
            return None

    async def _current_token(self) -> str:
        async with self._token_lock:
            if self._token is None:
                return await self._fetch_token()
            return self._token

    async def _renew_token(self, rejected: str) -> str:
        async with self._token_lock:
            # Another caller may already have replaced the rejected token.
            if self._token is not None and self._token != rejected:
                return self._token
            self._token = None
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        body = {"login": self._login, "password": self._password, "action": _GET_TOKEN}
        try:
            data = await self._post(None, _GET_TOKEN, body)
        except Tel2NoData as err:
            raise Tel2ApiError(str(err), None, _GET_TOKEN) from err
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise Tel2ConnectionError("Tel2 Get-Token response has no token")
        self._token = token
        if self._on_token_refreshed is not None:
            self._on_token_refreshed(token)
        return token

    async def _send(
        self,
        context: AccessContext,
        action: str,
        params: dict[str, Any] | None,
        token: str,
    ) -> Any:
        body = {
            "token": token,
            "action": action,
            "context": context,
            "params": params or {},
        }
        return await self._post(context, action, body)

    async def _post(
        self, context: AccessContext | None, action: str, body: dict[str, Any]
    ) -> Any:
        try:
            async with self._session.post(
                self._api_url, json=body, headers=_HEADERS, timeout=REQUEST_TIMEOUT
            ) as response:
                status = response.status
                raw = await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Tel2 %s %s: %s", context or "-", action, type(err).__name__)
            raise Tel2ConnectionError(
                f"Tel2 {action} failed: {type(err).__name__}"
            ) from err
        try:
            data = _parse_envelope(raw, status, context, action)
        except Tel2Error as err:
            _LOGGER.debug("Tel2 %s %s: %s", context or "-", action, type(err).__name__)
            raise
        _LOGGER.debug("Tel2 %s %s: success", context or "-", action)
        return data


def _parse_envelope(
    raw: bytes, status: int, context: AccessContext | None, action: str
) -> Any:
    try:
        envelope = json.loads(raw)
    except ValueError as err:
        raise Tel2ConnectionError(
            f"Tel2 {action} returned HTTP {status} without JSON"
        ) from err
    if not isinstance(envelope, dict) or "Result" not in envelope:
        raise Tel2ConnectionError(f"Tel2 {action} returned an unexpected response")
    result = str(envelope["Result"])
    message = str(envelope.get("Message") or "")
    if result == "-1" or envelope.get("Status") == "Error":
        if is_auth_message(message):
            raise Tel2AuthError(message)
        raise Tel2ApiError(message, context, action)
    if result == "1":
        return envelope.get("Data")
    if result == "0":
        raise Tel2NoData(message)
    raise Tel2ApiError(message, context, action)
