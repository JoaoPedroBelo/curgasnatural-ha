"""The CUR Gás Natural integration.

Author: João Belo
Independent open-source integration for the Portuguese natural-gas supplier of
last resort (Comercializador de Último Recurso) customer portal. Not affiliated
with Galp, Lisboagás or any CUR entity.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import CurGasNaturalCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CUR Gás Natural from a config entry."""
    _LOGGER.debug("Setting up CUR Gás Natural integration")

    coordinator = CurGasNaturalCoordinator(hass, {**entry.data, **entry.options})

    # Fail fast (ConfigEntryNotReady triggers HA retry) if the first poll fails.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Poll twice a day instead of on a periodic interval.
    coordinator.async_setup_schedule()
    entry.async_on_unload(coordinator.async_teardown_schedule)

    # The conversion factor is read at setup, so apply an options edit by
    # reloading the entry.
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    _LOGGER.info("CUR Gás Natural setup complete for %s", coordinator.contract_number)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading CUR Gás Natural integration")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: CurGasNaturalCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.close()

    return unload_ok
