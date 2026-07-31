"""Tests for the CUR Gás Natural config flow."""

from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.curgasnatural.api import (
    CurGasNaturalAuthError,
    CurGasNaturalConnectionError,
)
from custom_components.curgasnatural.config_flow import ConfigFlow, _tidy_address
from custom_components.curgasnatural.const import (
    CONF_ADDRESS,
    CONF_CONTRACT_ID,
    CONF_CONTRACT_NUMBER,
    CONF_CUI,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
)

from .conftest import TEST_CONTRACT_ID

CREDENTIALS = {CONF_EMAIL: "user@example.pt", CONF_PASSWORD: "secret"}

CONTRACT_A = {
    "guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "contractNumber": "34_00000000_00000000",
    "energyType": "GAS",
    "name": "R. EXAMPLE   1, 2, 3",
}
CONTRACT_B = {
    "guid": "11111111-2222-3333-4444-555555555555",
    "contractNumber": "31_00000000_00000000",
    "energyType": "GAS",
    "name": "R. OTHER   3º ESQ, 125, 3ESQ",
}


@pytest.fixture
def flow(hass):
    """A config flow wired to the test hass instance.

    ``context`` is normally a read-only mapping supplied by the flow manager;
    a hand-built handler needs a mutable one so ``async_set_unique_id`` works.
    """
    handler = ConfigFlow()
    handler.hass = hass
    handler.handler = DOMAIN
    handler.context = {}
    return handler


def patch_client(contracts, *, cui="PT1605000000000000XX", side_effect=None):
    """Patch the client the flow instantiates."""
    client = AsyncMock()
    client.async_list_contracts = AsyncMock(
        return_value=contracts, side_effect=side_effect
    )
    client.async_get_contract_info = AsyncMock(return_value={"CUI": cui} if cui else {})
    client.close = AsyncMock()
    return patch(
        "custom_components.curgasnatural.config_flow.CurGasNaturalClient",
        return_value=client,
    )


async def test_form_is_shown_first(flow):
    result = await flow.async_step_user()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_single_contract_skips_the_picker(flow):
    with patch_client([CONTRACT_A]):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "R. EXAMPLE 1, 2, 3"
    assert result["data"][CONF_CONTRACT_ID] == CONTRACT_A["guid"]
    assert result["data"][CONF_CONTRACT_NUMBER] == "34_00000000_00000000"
    assert result["data"][CONF_CUI] == "PT1605000000000000XX"
    assert result["data"][CONF_ADDRESS] == "R. EXAMPLE 1, 2, 3"
    # Credentials must be carried into the entry.
    assert result["data"][CONF_EMAIL] == "user@example.pt"


async def test_several_contracts_show_the_picker(flow):
    with patch_client([CONTRACT_A, CONTRACT_B]):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "contract"


async def test_picking_a_contract_creates_that_entry(flow):
    with patch_client([CONTRACT_A, CONTRACT_B]):
        await flow.async_step_user(CREDENTIALS)
        result = await flow.async_step_contract({CONF_CONTRACT_ID: CONTRACT_B["guid"]})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONTRACT_ID] == CONTRACT_B["guid"]
    assert result["data"][CONF_CONTRACT_NUMBER] == "31_00000000_00000000"


async def test_picking_an_unknown_contract_aborts(flow):
    with patch_client([CONTRACT_A, CONTRACT_B]):
        await flow.async_step_user(CREDENTIALS)
        result = await flow.async_step_contract({CONF_CONTRACT_ID: "gone"})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unknown_contract"


async def test_an_account_without_contracts_aborts(flow):
    with patch_client([]):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_contracts"


async def test_invalid_credentials_show_an_error(flow):
    with patch_client(None, side_effect=CurGasNaturalAuthError("bad_credentials")):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_an_unreachable_portal_shows_an_error(flow):
    with patch_client(None, side_effect=CurGasNaturalConnectionError("down")):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["errors"] == {"base": "cannot_connect"}


async def test_an_unexpected_error_is_reported_as_unknown(flow):
    with patch_client(None, side_effect=RuntimeError("boom")):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["errors"] == {"base": "unknown"}


async def test_setup_still_succeeds_when_the_cui_lookup_fails(flow):
    client = AsyncMock()
    client.async_list_contracts = AsyncMock(return_value=[CONTRACT_A])
    client.async_get_contract_info = AsyncMock(side_effect=RuntimeError("boom"))
    client.close = AsyncMock()

    with patch(
        "custom_components.curgasnatural.config_flow.CurGasNaturalClient",
        return_value=client,
    ):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_CUI not in result["data"]


async def test_the_email_is_trimmed(flow):
    with patch_client([CONTRACT_A]):
        result = await flow.async_step_user(
            {**CREDENTIALS, CONF_EMAIL: "  user@example.pt  "}
        )

    assert result["data"][CONF_EMAIL] == "user@example.pt"


def test_tidy_address_collapses_padding():
    assert _tidy_address("R. EXAMPLE   1,  2") == "R. EXAMPLE 1, 2"
    assert _tidy_address("   ") is None
    assert _tidy_address(None) is None


def _reauth_flow(hass, entry):
    """A flow already primed for re-authentication of ``entry``."""
    handler = ConfigFlow()
    handler.hass = hass
    handler.handler = DOMAIN
    handler.context = {"entry_id": entry.entry_id}
    return handler


async def test_reauth_asks_for_the_password(hass, mock_config_entry):
    with patch.object(
        hass.config_entries, "async_get_entry", return_value=mock_config_entry
    ):
        flow = _reauth_flow(hass, mock_config_entry)
        result = await flow.async_step_reauth(mock_config_entry.data)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    # The e-mail is shown so the user knows which account is being fixed.
    assert result["description_placeholders"] == {"email": "user@example.pt"}


async def test_reauth_stores_the_new_password_and_reloads(hass, mock_config_entry):
    with (
        patch.object(
            hass.config_entries, "async_get_entry", return_value=mock_config_entry
        ),
        patch.object(hass.config_entries, "async_update_entry") as update,
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload,
        patch_client([CONTRACT_A]),
    ):
        flow = _reauth_flow(hass, mock_config_entry)
        await flow.async_step_reauth(mock_config_entry.data)
        result = await flow.async_step_reauth_confirm({CONF_PASSWORD: "new-secret"})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert update.call_args.kwargs["data"][CONF_PASSWORD] == "new-secret"
    # The contract selection must survive a reauth untouched.
    assert update.call_args.kwargs["data"][CONF_CONTRACT_ID] == TEST_CONTRACT_ID
    reload.assert_awaited_once()


async def test_reauth_reprompts_when_the_new_password_is_also_wrong(
    hass, mock_config_entry
):
    with (
        patch.object(
            hass.config_entries, "async_get_entry", return_value=mock_config_entry
        ),
        patch.object(hass.config_entries, "async_update_entry") as update,
        patch_client(None, side_effect=CurGasNaturalAuthError("bad_credentials")),
    ):
        flow = _reauth_flow(hass, mock_config_entry)
        await flow.async_step_reauth(mock_config_entry.data)
        result = await flow.async_step_reauth_confirm({CONF_PASSWORD: "still-wrong"})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    update.assert_not_called()
