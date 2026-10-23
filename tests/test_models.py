"""Tests for Tel2 data models."""

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from custom_components.tel2 import models
from custom_components.tel2.const import CallEventType
from custom_components.tel2.models import (
    AccountBalance,
    CallEvent,
    LineState,
    Tel2Number,
)

FROZEN_MODELS = (
    models.Tel2Number,
    models.CustomerDetails,
    models.AccountBalance,
    models.PlanSummary,
    models.BundleUsage,
    models.AccountState,
    models.CallEvent,
    models.WebhookSpec,
    models.WebhookRegistration,
)
MUTABLE_MODELS = (models.LineState, models.LinesState)
ALL_MODELS = FROZEN_MODELS + MUTABLE_MODELS


@pytest.mark.parametrize("model", FROZEN_MODELS, ids=lambda m: m.__name__)
def test_value_models_are_frozen_and_slotted(model: type) -> None:
    params = model.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen
    assert "__slots__" in vars(model)


@pytest.mark.parametrize("model", MUTABLE_MODELS, ids=lambda m: m.__name__)
def test_state_models_are_slotted(model: type) -> None:
    assert dataclasses.is_dataclass(model)
    assert "__slots__" in vars(model)


def test_assigning_to_a_frozen_model_fails() -> None:
    number = Tel2Number(
        canonical="441632960123", tel2_id="01632960123", name="Line 1", extension=None
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        number.canonical = "441632960999"  # type: ignore[misc]


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_no_model_carries_a_password(model: type) -> None:
    names = {field.name.lower() for field in dataclasses.fields(model)}
    assert not {name for name in names if "password" in name or "parameter_1" in name}


def test_models_construct_with_expected_types() -> None:
    balance = AccountBalance(balance=Decimal("12.34"), available_balance=Decimal(10))
    event = CallEvent(
        number="441632960123",
        event_type=CallEventType.RINGING,
        direction="inbound",
        from_number="441632960999",
        to_number="441632960123",
        call_id="1",
        origin_id="1",
        start_time=datetime(2026, 10, 16, tzinfo=UTC),
        duration=None,
    )
    line = LineState(registered=True, prefs={"do_not_disturb_status": "off"})
    line.registered = False

    assert balance.balance == Decimal("12.34")
    assert event.event_type == "ringing"
    assert not line.registered
