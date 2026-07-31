"""Config flow for the CUR Gás Natural integration.

Setup is a live login followed by a contract picker: a CUR account can hold
several contracts (one per delivery point), and each is configured as its own
entry so it gets its own device, entities and consumption statistic.

There is no two-factor step — the portal challenges neither new IPs nor new
sessions, so polling stays fully headless once the entry exists.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    CurGasNaturalAuthError,
    CurGasNaturalClient,
    CurGasNaturalConnectionError,
)
from .const import (
    CONF_ADDRESS,
    CONF_CONTRACT_ID,
    CONF_CONTRACT_NUMBER,
    CONF_CONVERSION_FACTOR,
    CONF_CUI,
    CONF_EMAIL,
    CONF_PASSWORD,
    DEFAULT_CONVERSION_FACTOR,
    DOMAIN,
    MAX_CONVERSION_FACTOR,
    MIN_CONVERSION_FACTOR,
)

_LOGGER = logging.getLogger(__name__)

# Selectors rather than bare ``str``: a plain string renders as a *visible* text
# box, so the portal password would be shown in clear while being typed.
EMAIL_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): EMAIL_SELECTOR,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)

_WHITESPACE_RE = re.compile(r"\s+")


def _tidy_address(raw: Any) -> str | None:
    """Collapse the runs of spaces the portal pads addresses with."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _WHITESPACE_RE.sub(" ", raw).strip()


def _contract_label(contract: dict[str, Any]) -> str:
    """Build a picker label that distinguishes contracts at a glance."""
    address = _tidy_address(contract.get("name"))
    number = contract.get("contractNumber") or ""
    if address and number:
        return f"{address} ({number})"
    return address or str(number) or "?"


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CUR Gás Natural."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowHandler:
        """Expose the volume-to-energy conversion factor for editing."""
        return OptionsFlowHandler(config_entry)

    def __init__(self) -> None:
        """Initialise transient flow state."""
        self._creds: dict[str, str] = {}
        self._contracts: list[dict[str, Any]] = []
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect and validate the portal credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._creds = {
                CONF_EMAIL: user_input[CONF_EMAIL].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            contracts = await self._async_fetch_contracts(errors)
            if contracts is not None:
                if not contracts:
                    return self.async_abort(reason="no_contracts")
                self._contracts = contracts
                return await self.async_step_contract()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick which contract this entry tracks.

        A single-contract account skips the form: there is nothing to choose.
        """
        if user_input is None and len(self._contracts) == 1:
            user_input = {CONF_CONTRACT_ID: self._contracts[0]["guid"]}

        if user_input is not None:
            contract = next(
                (
                    c
                    for c in self._contracts
                    if c.get("guid") == user_input[CONF_CONTRACT_ID]
                ),
                None,
            )
            if contract is None:
                return self.async_abort(reason="unknown_contract")
            return await self._async_create_entry(contract)

        options = {
            c["guid"]: _contract_label(c) for c in self._contracts if c.get("guid")
        }
        return self.async_show_form(
            step_id="contract",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONTRACT_ID, default=next(iter(options), None)
                    ): vol.In(options)
                }
            ),
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Start re-authentication after the stored password stopped working."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for the current password and validate it with a live login."""
        assert self._reauth_entry is not None
        email = self._reauth_entry.data[CONF_EMAIL]
        errors: dict[str, str] = {}

        if user_input is not None:
            self._creds = {
                CONF_EMAIL: email,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            if await self._async_fetch_contracts(errors) is not None:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={**self._reauth_entry.data, **self._creds},
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            errors=errors,
            description_placeholders={"email": email},
        )

    async def _async_fetch_contracts(
        self, errors: dict[str, str]
    ) -> list[dict[str, Any]] | None:
        """Log in and list the account's contracts, or set ``errors`` and return None."""
        client = CurGasNaturalClient(
            email=self._creds[CONF_EMAIL], password=self._creds[CONF_PASSWORD]
        )
        try:
            return await client.async_list_contracts()
        except CurGasNaturalAuthError:
            errors["base"] = "invalid_auth"
        except CurGasNaturalConnectionError:
            errors["base"] = "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception during CUR login")
            errors["base"] = "unknown"
        finally:
            await client.close()
        return None

    async def _async_create_entry(self, contract: dict[str, Any]) -> FlowResult:
        """Create the entry for one contract, recording its delivery point."""
        contract_id = str(contract["guid"])
        await self.async_set_unique_id(contract_id)
        self._abort_if_unique_id_configured()

        address = _tidy_address(contract.get("name"))
        contract_number = str(contract.get("contractNumber") or contract_id)

        # The CUI is not in the contract list, so fetch it once here: it becomes
        # the device's serial number and lets each poll skip a round trip.
        cui = await self._async_fetch_cui(contract_id)

        data = {
            **self._creds,
            CONF_CONTRACT_ID: contract_id,
            CONF_CONTRACT_NUMBER: contract_number,
        }
        if address:
            data[CONF_ADDRESS] = address
        if cui:
            data[CONF_CUI] = cui

        return self.async_create_entry(
            title=address or f"CUR Gás Natural ({contract_number})", data=data
        )

    async def _async_fetch_cui(self, contract_id: str) -> str | None:
        """Return the contract's CUI, or ``None`` if the lookup does not work out.

        Setup must not fail over this: the coordinator resolves the CUI from its
        own poll when the entry does not carry one.
        """
        client = CurGasNaturalClient(
            email=self._creds[CONF_EMAIL], password=self._creds[CONF_PASSWORD]
        )
        try:
            info = await client.async_get_contract_info(contract_id)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Could not read the CUI at setup", exc_info=True)
            return None
        else:
            cui = info.get("CUI")
            return str(cui) if cui else None
        finally:
            await client.close()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Edit the kWh/m³ conversion factor after setup.

    Options are merged over entry data at setup, so this is what a user changes
    when the distributor revises the network's PCS.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Keep the entry under a private name.

        Assigning ``self.config_entry`` is deprecated in newer Home Assistant
        cores (it became a managed property), so this stays compatible with both.
        """
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show and store the conversion factor."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONVERSION_FACTOR,
                        default=float(
                            current.get(
                                CONF_CONVERSION_FACTOR, DEFAULT_CONVERSION_FACTOR
                            )
                        ),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=MIN_CONVERSION_FACTOR, max=MAX_CONVERSION_FACTOR),
                    )
                }
            ),
        )
