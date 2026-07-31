"""Tests for the CUR Gás Natural coordinator's normalisation and error mapping."""

from datetime import date
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.curgasnatural.api import (
    CurGasNaturalAuthError,
    CurGasNaturalConnectionError,
)
from custom_components.curgasnatural.const import (
    CONF_CONTRACT_ID,
    CONF_CONTRACT_NUMBER,
    CONF_CONVERSION_FACTOR,
    CONF_CUI,
    CONF_EMAIL,
    CONF_PASSWORD,
    DEFAULT_CONVERSION_FACTOR,
    POLL_HOURS,
)
from custom_components.curgasnatural.coordinator import (
    CurGasNaturalCoordinator,
    _parse_compact_date,
    _parse_iso_date,
    _parse_reading_date,
    _parse_readings,
    _to_float,
)

from .conftest import TEST_CONTRACT_ID, TEST_CONTRACT_NUMBER, TEST_CUI, TEST_EMAIL

TODAY = date(2026, 7, 31)

_CONFIG = {
    CONF_EMAIL: TEST_EMAIL,
    CONF_PASSWORD: "secret",
    CONF_CONTRACT_ID: TEST_CONTRACT_ID,
    CONF_CONTRACT_NUMBER: TEST_CONTRACT_NUMBER,
    CONF_CUI: TEST_CUI,
}


def test_normalise_extracts_meter_index_and_reading(raw_payload):
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY)

    assert data["available"] is True
    assert data["meter_index"] == 320.0
    assert data["last_reading_iso"] == "2026-07-30"
    assert data["last_reading_origin"] == "Leitura do cliente"


def test_normalise_computes_consumption_between_last_two_readings(raw_payload):
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY)

    # 320 m³ on 30-07 minus 300 m³ on 11-07, over 19 days.
    assert data["last_consumption"] == 20.0
    assert data["last_consumption_from"] == "2026-07-11"
    assert data["last_consumption_days"] == 19


def test_normalise_reads_next_reading_window(raw_payload):
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY)

    assert data["next_reading_start"] == date(2026, 8, 20)
    assert data["next_reading_end"] == date(2026, 8, 23)


def test_normalise_extracts_contract_and_distributor(raw_payload):
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY)

    assert data["contract_status"] == "ACTIVE"
    assert data["contract_tier"] == "ESCALAO_1"
    assert data["direct_debit"] is True
    assert data["delivery_point"].startswith("PT")
    assert data["distributor"] == "Lisboagás Comercialização, S.A."


def test_normalise_extracts_last_invoice(raw_payload):
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY)

    assert data["last_invoice_total"] == 12.34
    assert data["last_invoice_due"] == date(2026, 8, 17)
    assert data["last_invoice_period_start"] == date(2026, 6, 21)
    assert data["last_invoice_period_end"] == date(2026, 7, 20)
    assert data["last_invoice_status"] == "PENDING_PAYMENT"
    assert data["last_invoice_number"] == "FT K0000/00000000000"


def test_normalise_sums_only_pending_invoices_into_amount_due(raw_payload):
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY)

    assert data["amount_due"] == 12.34
    assert data["invoice_pending"] is True
    assert data["direct_debit_failed"] is False


def test_normalise_billed_12m_excludes_older_invoices(raw_payload):
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY)

    # 12.34 + 25.67; the 99.99 invoice emitted in 2024 falls outside the window.
    assert data["billed_12m"] == 38.01


def test_normalise_survives_empty_payload():
    data = CurGasNaturalCoordinator._normalise({}, TODAY, 11.2)

    # The conversion factor is always reported so the energy sensors can show it.
    assert data == {"available": True, "conversion_factor": 11.2}


def test_normalise_handles_missing_readings(raw_payload):
    raw_payload["readings"] = {"readings": []}
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY)

    assert "meter_index" not in data
    assert "last_consumption" not in data
    # The invoice side is independent and must still be populated.
    assert data["last_invoice_total"] == 12.34


def test_parse_readings_collapses_duplicate_dates_keeping_highest(raw_payload):
    readings = _parse_readings(raw_payload["readings"])

    same_day = [r for r in readings if r["iso"] == "2026-07-30"]
    assert len(same_day) == 1
    assert same_day[0]["index"] == 320.0


def test_parse_readings_is_sorted_oldest_first(raw_payload):
    readings = _parse_readings(raw_payload["readings"])

    assert [r["iso"] for r in readings] == sorted(r["iso"] for r in readings)
    assert readings[0]["iso"] == "2025-10-21"


def test_parse_readings_drops_unparseable_entries():
    payload = {
        "readings": [
            {"date": "not-a-date", "gv": "10"},
            {"date": "01-01-2026", "gv": "not-a-number"},
            {"date": "02-01-2026", "gv": "12"},
            "garbage",
        ]
    }

    assert _parse_readings(payload) == [
        {"iso": "2026-01-02", "index": 12.0, "origin": None}
    ]


def test_parse_readings_rejects_non_dict_payload():
    assert _parse_readings(None) == []
    assert _parse_readings({"readings": "nope"}) == []


def test_parse_reading_date_converts_day_first_format():
    assert _parse_reading_date("30-07-2026") == "2026-07-30"
    assert _parse_reading_date(" 01-01-2026 ") == "2026-01-01"
    assert _parse_reading_date("32-01-2026") is None
    assert _parse_reading_date("2026-07-30-1") is None
    assert _parse_reading_date(None) is None


def test_parse_compact_date_handles_reading_window_format():
    assert _parse_compact_date("20260820") == date(2026, 8, 20)
    assert _parse_compact_date("2026082") is None
    assert _parse_compact_date("20261320") is None
    assert _parse_compact_date(None) is None


def test_parse_iso_date_handles_the_offset_without_a_colon():
    assert _parse_iso_date("2026-08-17T00:00:00+0000") == date(2026, 8, 17)
    assert _parse_iso_date("2026-08-17") == date(2026, 8, 17)
    assert _parse_iso_date("nonsense") is None
    assert _parse_iso_date(None) is None


def test_to_float_tolerates_padding_and_commas():
    assert _to_float("320 ") == 320.0
    assert _to_float("12,34") == 12.34
    assert _to_float(3) == 3.0
    assert _to_float("abc") is None
    assert _to_float(None) is None


async def test_update_maps_auth_errors_to_reauth(hass, raw_payload):
    """An auth failure must prompt the user, not retry forever."""
    coordinator = CurGasNaturalCoordinator(hass, _CONFIG)
    coordinator.client.async_get_data = AsyncMock(
        side_effect=CurGasNaturalAuthError("bad_credentials")
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_update_maps_transport_errors_to_update_failed(hass):
    coordinator = CurGasNaturalCoordinator(hass, _CONFIG)
    coordinator.client.async_get_data = AsyncMock(
        side_effect=CurGasNaturalConnectionError("portal down")
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_update_normalises_and_imports_statistics(hass, raw_payload):
    coordinator = CurGasNaturalCoordinator(hass, _CONFIG)
    coordinator.client.async_get_data = AsyncMock(return_value=raw_payload)

    with patch(
        "custom_components.curgasnatural.coordinator."
        "async_import_consumption_statistics",
        AsyncMock(),
    ) as importer:
        data = await coordinator._async_update_data()

    assert data["meter_index"] == 320.0
    importer.assert_awaited_once()
    assert importer.await_args.args[1] == _CONFIG[CONF_CONTRACT_NUMBER]


async def test_a_statistics_failure_does_not_fail_the_poll(hass, raw_payload):
    """Sensors must still update when the recorder misbehaves."""
    coordinator = CurGasNaturalCoordinator(hass, _CONFIG)
    coordinator.client.async_get_data = AsyncMock(return_value=raw_payload)

    with patch(
        "custom_components.curgasnatural.coordinator."
        "async_import_consumption_statistics",
        AsyncMock(side_effect=RuntimeError("recorder not ready")),
    ):
        data = await coordinator._async_update_data()

    assert data["meter_index"] == 320.0


async def test_no_readings_skips_the_statistics_import(hass, raw_payload):
    raw_payload["readings"] = {"readings": []}
    coordinator = CurGasNaturalCoordinator(hass, _CONFIG)
    coordinator.client.async_get_data = AsyncMock(return_value=raw_payload)

    with patch(
        "custom_components.curgasnatural.coordinator."
        "async_import_consumption_statistics",
        AsyncMock(),
    ) as importer:
        await coordinator._async_update_data()

    importer.assert_not_awaited()


def test_schedule_registers_one_refresh_per_poll_hour(hass):
    coordinator = CurGasNaturalCoordinator(hass, _CONFIG)

    coordinator.async_setup_schedule()
    assert len(coordinator._unsub_schedule) == len(POLL_HOURS)

    coordinator.async_teardown_schedule()
    assert coordinator._unsub_schedule == []


def test_normalise_mirrors_volumes_in_energy(raw_payload):
    """The supplier bills kWh, so every m³ figure needs its energy twin."""
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY, 11.20808)

    assert data["meter_index"] == 320.0
    assert data["meter_index_energy"] == round(320.0 * 11.20808, 2)
    assert data["last_consumption"] == 20.0
    assert data["last_consumption_energy"] == round(20.0 * 11.20808, 2)
    assert data["conversion_factor"] == 11.20808


def test_normalise_falls_back_to_the_default_factor(raw_payload):
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY)

    assert data["conversion_factor"] == DEFAULT_CONVERSION_FACTOR


@pytest.mark.parametrize("bad", [None, 0, -1, "abc", ""])
def test_a_nonsensical_configured_factor_falls_back(hass, bad):
    """A zero or negative factor would silently zero out every energy figure."""
    coordinator = CurGasNaturalCoordinator(
        hass, {**_CONFIG, CONF_CONVERSION_FACTOR: bad}
    )

    assert coordinator.conversion_factor == DEFAULT_CONVERSION_FACTOR


def test_a_configured_factor_is_used(hass):
    coordinator = CurGasNaturalCoordinator(
        hass, {**_CONFIG, CONF_CONVERSION_FACTOR: 11.20808}
    )

    assert coordinator.conversion_factor == 11.20808


def test_normalise_builds_an_invoice_series_keyed_by_period_end(raw_payload):
    """Cost belongs to the period it bills, not the day the invoice was issued."""
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY, 11.20808)

    series = data["invoice_series"]
    assert [e["iso"] for e in series] == sorted(e["iso"] for e in series)
    # endBillingPeriod of the three fixture invoices.
    assert series == [
        {"iso": "2024-06-20", "total": 99.99},
        {"iso": "2026-06-20", "total": 25.67},
        {"iso": "2026-07-20", "total": 12.34},
    ]


def test_invoices_closing_on_the_same_day_are_merged(raw_payload):
    raw_payload["invoices"]["invoices"].append(
        {
            "emissionDate": "2026-07-23T00:00:00+0000",
            "endBillingPeriod": "2026-07-20T00:00:00+0000",
            "paymentStatus": "PAID",
            "totalValue": "2.50",
        }
    )
    data = CurGasNaturalCoordinator._normalise(raw_payload, TODAY, 11.20808)

    same_day = [e for e in data["invoice_series"] if e["iso"] == "2026-07-20"]
    assert same_day == [{"iso": "2026-07-20", "total": 14.84}]
