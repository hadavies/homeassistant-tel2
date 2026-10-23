"""Data models for the Tel2 integration."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from .const import CallEventType

type AccessContext = Literal["account", "voice", "api"]


@dataclass(frozen=True, slots=True)
class Tel2Number:
    """A phone number on the Tel2 account."""

    canonical: str
    tel2_id: str
    name: str | None
    extension: str | None


@dataclass(frozen=True, slots=True)
class CustomerDetails:
    """Tel2 customer account details."""

    account_code: str
    customer_name: str
    company_name: str | None
    status: str


@dataclass(frozen=True, slots=True)
class AccountBalance:
    """Tel2 account balance in GBP."""

    balance: Decimal
    available_balance: Decimal


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """Current Tel2 plan."""

    alias: str
    description: str


@dataclass(frozen=True, slots=True)
class BundleUsage:
    """Bundle minutes for one number in the current bill cycle."""

    used_minutes: int
    total_minutes: int


@dataclass(frozen=True, slots=True)
class AccountState:
    """Account-level state from the account coordinator."""

    details: CustomerDetails
    balance: AccountBalance | None
    plan: PlanSummary | None
    numbers: dict[str, Tel2Number]
    bundles: dict[str, BundleUsage]


@dataclass(slots=True)
class LineState:
    """Registration and non-secret prefs for one number."""

    registered: bool
    prefs: dict[str, str]


@dataclass(slots=True)
class LinesState:
    """Line state for every number, keyed by canonical number."""

    lines: dict[str, LineState]


@dataclass(frozen=True, slots=True)
class CallEvent:
    """A call event received from a Tel2 webhook."""

    number: str
    event_type: CallEventType
    direction: Literal["inbound", "outbound"]
    from_number: str
    to_number: str
    call_id: str
    origin_id: str
    start_time: datetime | None
    duration: int | None


@dataclass(frozen=True, slots=True)
class WebhookSpec:
    """Desired Tel2 webhook registration."""

    name: str
    url: str
    secret_header: str
    secret_token: str


@dataclass(frozen=True, slots=True)
class WebhookRegistration:
    """A webhook registration as Tel2 reports it."""

    tel2_id: str
    name: str
    url: str
