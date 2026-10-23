"""Capture anonymised read-only Tel2 API responses as test fixtures."""

import argparse
import asyncio
from collections.abc import Mapping, Sequence
import getpass
import json
import os
from pathlib import Path
import secrets
import sys
from types import MappingProxyType
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import aiohttp

    from custom_components.tel2.const import API_URL
    from scripts.anonymise import LeakError, anonymise_responses
except ImportError as err:
    sys.exit(
        "Cannot import custom_components.tel2 (it loads Home Assistant). Run this "
        "inside the project's dev environment: "
        f".venv/bin/python scripts/capture_fixtures.py ({err})"
    )

TOKEN_ACTION: Final = "Get-Token"  # noqa: S105
BUNDLE_ACTION: Final = "Get-Line-Bundle-Usage"
INVALID_TOKEN_ACTION: Final = "Get-Account-Balance"  # noqa: S105
REQUEST_TIMEOUT_SECONDS: Final = 30

READ_ONLY_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        TOKEN_ACTION: "",
        "Get-Customer-Details": "account",
        "Get-Account-Balance": "account",
        "Get-Current-Plan-Summary": "account",
        BUNDLE_ACTION: "account",
        "Get-Numbers": "voice",
        "Get-Line-Names": "voice",
        "Get-Subscriber-Status": "voice",
        "Get-All-Pref-Summary": "voice",
        "Get-Webhooks": "api",
    }
)

_EMPTY_NUMBER_SEARCH: Final = {
    "orderby": "",
    "orderdirection": "",
    "searchby": "",
    "searchterm": "",
}
CAPTURES: Final[tuple[tuple[str, str, Mapping[str, str]], ...]] = (
    ("get_customer_details.json", "Get-Customer-Details", {}),
    ("get_account_balance.json", "Get-Account-Balance", {}),
    ("get_current_plan_summary.json", "Get-Current-Plan-Summary", {}),
    ("get_numbers.json", "Get-Numbers", _EMPTY_NUMBER_SEARCH),
    ("get_line_names.json", "Get-Line-Names", {}),
    ("get_subscriber_status.json", "Get-Subscriber-Status", {}),
    ("get_all_pref_summary.json", "Get-All-Pref-Summary", {}),
    ("get_webhooks.json", "Get-Webhooks", {}),
)
ACTION_BY_FILE: Final[Mapping[str, str]] = MappingProxyType(
    {
        "get_token.json": TOKEN_ACTION,
        **{name: action for name, action, _ in CAPTURES},
        "get_line_bundle_usage.json": BUNDLE_ACTION,
        "error_wrong_password.json": TOKEN_ACTION,
        "error_invalid_token.json": INVALID_TOKEN_ACTION,
    }
)


def build_request(
    action: str,
    *,
    token: str | None = None,
    params: Mapping[str, str] | None = None,
    login: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    """Return the JSON body for an allowlisted read-only action."""
    if action not in READ_ONLY_ACTIONS:
        raise ValueError(f"Action is not in the read-only allowlist: {action}")
    if params and "reset" in params:
        raise ValueError("The reset parameter rotates the token and is never sent")
    if action == TOKEN_ACTION:
        return {"login": login, "password": password, "action": action}
    return {
        "token": token,
        "action": action,
        "context": READ_ONLY_ACTIONS[action],
        "params": dict(params or {}),
    }


async def _post(session: aiohttp.ClientSession, body: Mapping[str, Any]) -> Any:
    """POST one request and return the decoded JSON response."""
    async with session.post(API_URL, json=body) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


def _token_from(response: Any) -> str:
    """Return the session token from a Get-Token response or exit."""
    data = response.get("Data") if isinstance(response, dict) else None
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        status = response.get("Status") if isinstance(response, dict) else None
        sys.exit(f"Get-Token did not return a token (Status: {status}).")
    return token


def _line_numbers(response: Any) -> list[str]:
    """Return the ServiceName of every line in a Get-Numbers response."""
    data = response.get("Data") if isinstance(response, dict) else None
    rows = data.values() if isinstance(data, dict) else data or []
    numbers: list[str] = []
    for row in rows:
        number = row.get("ServiceName") if isinstance(row, dict) else None
        if isinstance(number, str) and number and number not in numbers:
            numbers.append(number)
    return numbers


async def capture(login: str, password: str) -> tuple[dict[str, Any], str]:
    """Call every allowlisted action and return the raw responses and token."""
    responses: dict[str, Any] = {}
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        token_response = await _post(
            session, build_request(TOKEN_ACTION, login=login, password=password)
        )
        token = _token_from(token_response)
        responses["get_token.json"] = token_response
        for name, action, params in CAPTURES:
            responses[name] = await _post(
                session, build_request(action, token=token, params=params)
            )
        responses["get_line_bundle_usage.json"] = {
            number: await _post(
                session,
                build_request(BUNDLE_ACTION, token=token, params={"line": number}),
            )
            for number in _line_numbers(responses["get_numbers.json"])
        }
        responses["error_invalid_token.json"] = await _post(
            session,
            build_request(INVALID_TOKEN_ACTION, token="invalid"),  # noqa: S106
        )
        responses["error_wrong_password.json"] = await _post(
            session,
            build_request(
                TOKEN_ACTION, login=login, password=secrets.token_urlsafe(24)
            ),
        )
    return responses, token


def write_fixtures(out: Path, fixtures: Mapping[str, Any]) -> None:
    """Write each fixture and an index marking them as captured."""
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for name, data in fixtures.items():
        (out / name).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        entry = {"file": name, "action": ACTION_BY_FILE[name], "source": "captured"}
        if ACTION_BY_FILE[name] == BUNDLE_ACTION:
            entry["keyed_by"] = "line"
        index.append(entry)
    (out / "index.json").write_text(
        json.dumps({"files": index}, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Prompt for credentials, capture, anonymise and write the fixtures."""
    parser = argparse.ArgumentParser(
        description=(
            "Call read-only Tel2 API actions with a portal login and write "
            "anonymised responses as test fixtures. Review the output before "
            "committing it."
        )
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "tests" / "fixtures" / "captured",
        help="directory to write fixtures to (default: tests/fixtures/captured)",
    )
    args = parser.parse_args(argv)
    login = (
        os.environ.get("TEL2_LOGIN") or getpass.getpass("Tel2 portal login: ")
    ).strip()
    password = os.environ.get("TEL2_PASSWORD") or getpass.getpass(
        "Tel2 portal password: "
    )
    try:
        responses, token = asyncio.run(capture(login, password))
    except (aiohttp.ClientError, TimeoutError) as err:
        sys.exit(f"Tel2 request failed: {type(err).__name__}")
    try:
        fixtures = anonymise_responses(responses, secrets=(login, password, token))
    except LeakError as err:
        sys.exit(
            "Anonymisation check failed, nothing was written. Offending paths:\n"
            + "\n".join(err.paths)
        )
    write_fixtures(args.out, fixtures)
    print(
        f"Wrote {len(fixtures)} anonymised fixtures to {args.out}. Review before committing."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
