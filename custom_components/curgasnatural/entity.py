"""Shared entity base for the CUR Gás Natural integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADDRESS,
    CONF_CONTRACT_NUMBER,
    CONF_CUI,
    DATA_DISTRIBUTOR,
    DOMAIN,
    PORTAL_ORIGIN,
)
from .coordinator import CurGasNaturalCoordinator

DEFAULT_MANUFACTURER = "CUR Gás Natural"


class CurGasNaturalEntity(CoordinatorEntity[CurGasNaturalCoordinator]):
    """Identity and device info shared by every CUR entity.

    One device per config entry, i.e. per contract/delivery point, named after
    the supply address so an account with several contracts stays readable.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CurGasNaturalCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise identity and device info."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"

        contract_number = entry.data.get(CONF_CONTRACT_NUMBER) or entry.entry_id
        address = entry.data.get(CONF_ADDRESS)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=address or f"{DEFAULT_MANUFACTURER} {contract_number}",
            # The distributor is the party that actually reads the meter; it is
            # only known after the first poll, so fall back to the brand.
            manufacturer=(coordinator.data or {}).get(DATA_DISTRIBUTOR)
            or DEFAULT_MANUFACTURER,
            model="Mercado Regulado (CUR)",
            serial_number=entry.data.get(CONF_CUI),
            configuration_url=f"{PORTAL_ORIGIN}/area-privada",
        )
