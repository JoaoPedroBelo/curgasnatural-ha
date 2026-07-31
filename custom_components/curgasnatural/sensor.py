"""Sensor platform for the CUR Gás Natural integration.

Volumes are in cubic metres (m³) — the unit the meter index (``gv``) is read in —
and money in EUR. All verified live.

The Gas dashboard is fed by the ``curgasnatural:consumption_<contract>``
long-term statistic imported by the coordinator (see ``statistics.py``), not by a
live ``total_increasing`` sensor: readings are backdated and sparse, so a live
sensor would attribute a whole month's gas to the poll hour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CURRENCY_EURO,
    EntityCategory,
    UnitOfEnergy,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_CONTRACT_NUMBER,
    ATTR_CONVERSION_FACTOR,
    ATTR_CUI,
    ATTR_DAYS,
    ATTR_DISTRIBUTOR,
    ATTR_INVOICE_NUMBER,
    ATTR_ORIGIN,
    ATTR_PAYMENT_STATUS,
    ATTR_PERIOD_END,
    ATTR_PERIOD_START,
    ATTR_READING_DATE,
    ATTR_TIER,
    DATA_AMOUNT_DUE,
    DATA_BILLED_12M,
    DATA_CONTRACT_NUMBER,
    DATA_CONTRACT_STATUS,
    DATA_CONTRACT_TIER,
    DATA_CONVERSION_FACTOR,
    DATA_DELIVERY_POINT,
    DATA_DISTRIBUTOR,
    DATA_LAST_CONSUMPTION,
    DATA_LAST_CONSUMPTION_DAYS,
    DATA_LAST_CONSUMPTION_ENERGY,
    DATA_LAST_CONSUMPTION_FROM,
    DATA_LAST_INVOICE_DUE,
    DATA_LAST_INVOICE_NUMBER,
    DATA_LAST_INVOICE_PERIOD_END,
    DATA_LAST_INVOICE_PERIOD_START,
    DATA_LAST_INVOICE_STATUS,
    DATA_LAST_INVOICE_TOTAL,
    DATA_LAST_READING_ISO,
    DATA_LAST_READING_ORIGIN,
    DATA_METER_INDEX,
    DATA_METER_INDEX_ENERGY,
    DATA_NEXT_READING_END,
    DATA_NEXT_READING_START,
    DOMAIN,
    SENSOR_AMOUNT_DUE,
    SENSOR_BILLED_12M,
    SENSOR_LAST_CONSUMPTION,
    SENSOR_LAST_CONSUMPTION_ENERGY,
    SENSOR_LAST_INVOICE_DUE,
    SENSOR_LAST_INVOICE_TOTAL,
    SENSOR_LAST_READING_DATE,
    SENSOR_METER_INDEX,
    SENSOR_METER_INDEX_ENERGY,
    SENSOR_NEXT_READING_END,
    SENSOR_NEXT_READING_START,
)
from .coordinator import CurGasNaturalCoordinator
from .entity import CurGasNaturalEntity


@dataclass(frozen=True, kw_only=True)
class CurSensorDescription(SensorEntityDescription):
    """Describes a CUR sensor backed by a single ``coordinator.data`` key."""

    data_key: str
    # ``attribute name -> coordinator.data key``, exposed as extra attributes.
    attribute_keys: tuple[tuple[str, str], ...] = ()


SENSORS: tuple[CurSensorDescription, ...] = (
    CurSensorDescription(
        key=SENSOR_METER_INDEX,
        translation_key=SENSOR_METER_INDEX,
        data_key=DATA_METER_INDEX,
        icon="mdi:gauge",
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        # The meter index is an absolute, never-resetting counter, but it is not
        # ``total_increasing``: the Gas dashboard is driven by the imported
        # statistic instead, so this is purely informative.
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        attribute_keys=(
            (ATTR_READING_DATE, DATA_LAST_READING_ISO),
            (ATTR_ORIGIN, DATA_LAST_READING_ORIGIN),
            (ATTR_CUI, DATA_DELIVERY_POINT),
            (ATTR_CONTRACT_NUMBER, DATA_CONTRACT_NUMBER),
        ),
    ),
    CurSensorDescription(
        key=SENSOR_LAST_CONSUMPTION,
        translation_key=SENSOR_LAST_CONSUMPTION,
        data_key=DATA_LAST_CONSUMPTION,
        icon="mdi:fire",
        # Deliberately no device_class. This is the volume consumed *between two
        # past readings* — a period delta, not a meter. Home Assistant only accepts
        # ``total``/``total_increasing`` for SensorDeviceClass.GAS (see
        # DEVICE_CLASS_STATE_CLASSES), so declaring GAS here would be rejected at
        # runtime, and declaring ``total`` would falsely claim the value accumulates.
        # ``volume_storage`` would validate but means "volume held in a vessel".
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attribute_keys=(
            (ATTR_PERIOD_START, DATA_LAST_CONSUMPTION_FROM),
            (ATTR_PERIOD_END, DATA_LAST_READING_ISO),
            (ATTR_DAYS, DATA_LAST_CONSUMPTION_DAYS),
        ),
    ),
    CurSensorDescription(
        key=SENSOR_METER_INDEX_ENERGY,
        translation_key=SENSOR_METER_INDEX_ENERGY,
        data_key=DATA_METER_INDEX_ENERGY,
        icon="mdi:meter-gas",
        # The billed equivalent of the meter index: m³ x the network's kWh/m³.
        # ``energy`` rather than ``gas`` because HA only allows m³/ft³/CCF for gas.
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        attribute_keys=(
            (ATTR_READING_DATE, DATA_LAST_READING_ISO),
            (ATTR_CONVERSION_FACTOR, DATA_CONVERSION_FACTOR),
        ),
    ),
    CurSensorDescription(
        key=SENSOR_LAST_CONSUMPTION_ENERGY,
        translation_key=SENSOR_LAST_CONSUMPTION_ENERGY,
        data_key=DATA_LAST_CONSUMPTION_ENERGY,
        icon="mdi:fire",
        # A period delta, so no device class - see the volume sensor above.
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attribute_keys=(
            (ATTR_PERIOD_START, DATA_LAST_CONSUMPTION_FROM),
            (ATTR_PERIOD_END, DATA_LAST_READING_ISO),
            (ATTR_DAYS, DATA_LAST_CONSUMPTION_DAYS),
            (ATTR_CONVERSION_FACTOR, DATA_CONVERSION_FACTOR),
        ),
    ),
    CurSensorDescription(
        key=SENSOR_LAST_READING_DATE,
        translation_key=SENSOR_LAST_READING_DATE,
        data_key=DATA_LAST_READING_ISO,
        icon="mdi:calendar-check",
        device_class=SensorDeviceClass.DATE,
        attribute_keys=((ATTR_ORIGIN, DATA_LAST_READING_ORIGIN),),
    ),
    CurSensorDescription(
        key=SENSOR_NEXT_READING_START,
        translation_key=SENSOR_NEXT_READING_START,
        data_key=DATA_NEXT_READING_START,
        icon="mdi:calendar-start",
        device_class=SensorDeviceClass.DATE,
    ),
    CurSensorDescription(
        key=SENSOR_NEXT_READING_END,
        translation_key=SENSOR_NEXT_READING_END,
        data_key=DATA_NEXT_READING_END,
        icon="mdi:calendar-end",
        device_class=SensorDeviceClass.DATE,
    ),
    CurSensorDescription(
        key=SENSOR_LAST_INVOICE_TOTAL,
        translation_key=SENSOR_LAST_INVOICE_TOTAL,
        data_key=DATA_LAST_INVOICE_TOTAL,
        icon="mdi:receipt-text",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        attribute_keys=(
            (ATTR_INVOICE_NUMBER, DATA_LAST_INVOICE_NUMBER),
            (ATTR_PAYMENT_STATUS, DATA_LAST_INVOICE_STATUS),
            (ATTR_PERIOD_START, DATA_LAST_INVOICE_PERIOD_START),
            (ATTR_PERIOD_END, DATA_LAST_INVOICE_PERIOD_END),
        ),
    ),
    CurSensorDescription(
        key=SENSOR_LAST_INVOICE_DUE,
        translation_key=SENSOR_LAST_INVOICE_DUE,
        data_key=DATA_LAST_INVOICE_DUE,
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.DATE,
    ),
    CurSensorDescription(
        key=SENSOR_AMOUNT_DUE,
        translation_key=SENSOR_AMOUNT_DUE,
        data_key=DATA_AMOUNT_DUE,
        icon="mdi:cash-clock",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
    ),
    CurSensorDescription(
        key=SENSOR_BILLED_12M,
        translation_key=SENSOR_BILLED_12M,
        data_key=DATA_BILLED_12M,
        icon="mdi:chart-timeline-variant",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
    ),
    CurSensorDescription(
        key="contract_status",
        translation_key="contract_status",
        data_key=DATA_CONTRACT_STATUS,
        icon="mdi:file-document-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        attribute_keys=(
            (ATTR_CONTRACT_NUMBER, DATA_CONTRACT_NUMBER),
            (ATTR_CUI, DATA_DELIVERY_POINT),
            (ATTR_TIER, DATA_CONTRACT_TIER),
            (ATTR_DISTRIBUTOR, DATA_DISTRIBUTOR),
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CUR Gás Natural sensors."""
    coordinator: CurGasNaturalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        CurGasNaturalSensor(coordinator, entry, description) for description in SENSORS
    )


class CurGasNaturalSensor(CurGasNaturalEntity, SensorEntity):
    """A sensor reading one normalised value out of ``coordinator.data``."""

    entity_description: CurSensorDescription

    def __init__(
        self,
        coordinator: CurGasNaturalCoordinator,
        entry: ConfigEntry,
        description: CurSensorDescription,
    ) -> None:
        """Initialise the sensor from its description."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | date | None:
        """Return the value behind this sensor's ``coordinator.data`` key."""
        value = self.coordinator.data.get(self.entity_description.data_key)
        if (
            self.entity_description.device_class is SensorDeviceClass.DATE
            and isinstance(value, str)
        ):
            # Reading dates are normalised to ISO strings; a DATE sensor needs a
            # real ``date`` object.
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the description's extra attributes, omitting missing ones."""
        if not self.entity_description.attribute_keys:
            return None
        attributes = {
            name: self.coordinator.data.get(key)
            for name, key in self.entity_description.attribute_keys
        }
        return {k: v for k, v in attributes.items() if v is not None} or None
