"""Binary sensor platform for the CUR Gás Natural integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BINARY_SENSOR_AVAILABLE,
    BINARY_SENSOR_DIRECT_DEBIT_FAILED,
    BINARY_SENSOR_INVOICE_PENDING,
    DATA_AVAILABLE,
    DATA_DIRECT_DEBIT_FAILED,
    DATA_INVOICE_PENDING,
    DOMAIN,
)
from .coordinator import CurGasNaturalCoordinator
from .entity import CurGasNaturalEntity


@dataclass(frozen=True, kw_only=True)
class CurBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a CUR binary sensor backed by a ``coordinator.data`` key."""

    data_key: str
    # When true the entity also requires the last poll to have succeeded, which
    # is what makes the availability sensor differ from the rest.
    require_poll_success: bool = False


BINARY_SENSORS: tuple[CurBinarySensorDescription, ...] = (
    CurBinarySensorDescription(
        key=BINARY_SENSOR_INVOICE_PENDING,
        translation_key=BINARY_SENSOR_INVOICE_PENDING,
        data_key=DATA_INVOICE_PENDING,
        icon="mdi:cash-clock",
    ),
    CurBinarySensorDescription(
        key=BINARY_SENSOR_DIRECT_DEBIT_FAILED,
        translation_key=BINARY_SENSOR_DIRECT_DEBIT_FAILED,
        data_key=DATA_DIRECT_DEBIT_FAILED,
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    CurBinarySensorDescription(
        key=BINARY_SENSOR_AVAILABLE,
        translation_key=BINARY_SENSOR_AVAILABLE,
        data_key=DATA_AVAILABLE,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        require_poll_success=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CUR Gás Natural binary sensors."""
    coordinator: CurGasNaturalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        CurGasNaturalBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
    )


class CurGasNaturalBinarySensor(CurGasNaturalEntity, BinarySensorEntity):
    """A binary sensor reading one flag out of ``coordinator.data``."""

    entity_description: CurBinarySensorDescription

    def __init__(
        self,
        coordinator: CurGasNaturalCoordinator,
        entry: ConfigEntry,
        description: CurBinarySensorDescription,
    ) -> None:
        """Initialise the binary sensor from its description."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the flag behind this sensor's ``coordinator.data`` key."""
        value = bool(self.coordinator.data.get(self.entity_description.data_key))
        if self.entity_description.require_poll_success:
            return value and bool(self.coordinator.last_update_success)
        return value
