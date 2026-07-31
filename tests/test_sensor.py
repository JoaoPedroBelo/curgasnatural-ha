"""Tests for the CUR Gás Natural entities."""

from datetime import date

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.curgasnatural.binary_sensor import (
    BINARY_SENSORS,
    CurGasNaturalBinarySensor,
)
from custom_components.curgasnatural.sensor import SENSORS, CurGasNaturalSensor


def _sensor(coordinator, entry, key):
    """Build the sensor with the given description key."""
    description = next(d for d in SENSORS if d.key == key)
    return CurGasNaturalSensor(coordinator, entry, description)


def _binary_sensor(coordinator, entry, key):
    """Build the binary sensor with the given description key."""
    description = next(d for d in BINARY_SENSORS if d.key == key)
    return CurGasNaturalBinarySensor(coordinator, entry, description)


def test_every_sensor_has_a_unique_id(mock_coordinator, mock_config_entry):
    ids = [
        CurGasNaturalSensor(mock_coordinator, mock_config_entry, d).unique_id
        for d in SENSORS
    ]

    assert len(ids) == len(set(ids))
    assert all(i.startswith("test_entry_id_") for i in ids)


def test_meter_index_reports_the_index_in_cubic_metres(
    mock_coordinator, mock_config_entry
):
    sensor = _sensor(mock_coordinator, mock_config_entry, "meter_index")

    assert sensor.native_value == 320.0
    assert sensor.device_class is SensorDeviceClass.GAS
    assert sensor.native_unit_of_measurement == "m³"


def test_meter_index_exposes_the_reading_provenance(
    mock_coordinator, mock_config_entry
):
    sensor = _sensor(mock_coordinator, mock_config_entry, "meter_index")

    attributes = sensor.extra_state_attributes
    assert attributes["reading_date"] == "2026-07-30"
    assert attributes["origin"] == "Leitura do cliente"
    assert attributes["cui"].startswith("PT")


def test_last_consumption_exposes_the_period_it_covers(
    mock_coordinator, mock_config_entry
):
    sensor = _sensor(mock_coordinator, mock_config_entry, "last_consumption")

    assert sensor.native_value == 20.0
    assert sensor.extra_state_attributes == {
        "period_start": "2026-07-11",
        "period_end": "2026-07-30",
        "days": 19,
    }


def test_date_sensors_convert_iso_strings_to_dates(mock_coordinator, mock_config_entry):
    sensor = _sensor(mock_coordinator, mock_config_entry, "last_reading_date")

    assert sensor.native_value == date(2026, 7, 30)


def test_date_sensors_return_none_for_an_unparseable_value(
    mock_coordinator, mock_config_entry
):
    mock_coordinator.data["last_reading_iso"] = "not-a-date"
    sensor = _sensor(mock_coordinator, mock_config_entry, "last_reading_date")

    assert sensor.native_value is None


def test_monetary_sensors_report_euros(mock_coordinator, mock_config_entry):
    invoice = _sensor(mock_coordinator, mock_config_entry, "last_invoice_total")
    due = _sensor(mock_coordinator, mock_config_entry, "amount_due")

    assert invoice.native_value == 12.34
    assert invoice.device_class is SensorDeviceClass.MONETARY
    assert invoice.native_unit_of_measurement == "€"
    assert due.native_value == 12.34


def test_sensors_return_none_when_the_value_is_missing(
    mock_coordinator, mock_config_entry
):
    mock_coordinator.data = {"available": True}
    sensor = _sensor(mock_coordinator, mock_config_entry, "meter_index")

    assert sensor.native_value is None


def test_attributes_omit_missing_values(mock_coordinator, mock_config_entry):
    mock_coordinator.data.pop("last_consumption_days")
    sensor = _sensor(mock_coordinator, mock_config_entry, "last_consumption")

    assert "days" not in sensor.extra_state_attributes


def test_attributes_are_none_when_the_description_declares_none(
    mock_coordinator, mock_config_entry
):
    sensor = _sensor(mock_coordinator, mock_config_entry, "amount_due")

    assert sensor.extra_state_attributes is None


def test_device_info_is_named_after_the_supply_address(
    mock_coordinator, mock_config_entry
):
    sensor = _sensor(mock_coordinator, mock_config_entry, "meter_index")

    device = sensor.device_info
    assert device["name"] == "R. EXAMPLE 1, 2, 3"
    assert device["manufacturer"] == "Lisboagás Comercialização, S.A."
    assert device["serial_number"].startswith("PT")


def test_all_entities_share_one_device(mock_coordinator, mock_config_entry):
    identifiers = {
        tuple(
            CurGasNaturalSensor(mock_coordinator, mock_config_entry, d).device_info[
                "identifiers"
            ]
        )
        for d in SENSORS
    }

    assert len(identifiers) == 1


def test_invoice_pending_binary_sensor_follows_the_flag(
    mock_coordinator, mock_config_entry
):
    sensor = _binary_sensor(mock_coordinator, mock_config_entry, "invoice_pending")

    assert sensor.is_on is True

    mock_coordinator.data["invoice_pending"] = False
    assert sensor.is_on is False


def test_direct_debit_failed_binary_sensor_is_off_when_all_is_well(
    mock_coordinator, mock_config_entry
):
    sensor = _binary_sensor(mock_coordinator, mock_config_entry, "direct_debit_failed")

    assert sensor.is_on is False


def test_availability_sensor_also_requires_the_poll_to_have_succeeded(
    mock_coordinator, mock_config_entry
):
    sensor = _binary_sensor(mock_coordinator, mock_config_entry, "available")

    assert sensor.is_on is True

    mock_coordinator.last_update_success = False
    assert sensor.is_on is False


def test_availability_sensor_is_disabled_by_default():
    description = next(d for d in BINARY_SENSORS if d.key == "available")

    assert description.entity_registry_enabled_default is False
