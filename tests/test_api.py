"""Tests for the CUR Gás Natural API client.

The OAuth2 authorization-code + PKCE handshake is the fragile part of this
integration, so these tests pin down the whole chain — including the failure
modes seen live (``?error=bad_credentials``, a stale bearer token).
"""

from datetime import date
import re

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.curgasnatural.api import (
    CurGasNaturalAuthError,
    CurGasNaturalClient,
    CurGasNaturalConnectionError,
)
from custom_components.curgasnatural.const import (
    AUTH_BASE_URL,
    CSRF_PATH,
    LOGIN_PATH,
    OAUTH_REDIRECT_URI,
    OCC_BASE_SITE,
    OCC_BASE_URL,
    TOKEN_PATH,
)

AUTHORIZE_URL = re.compile(
    r"^https://api-portal\.curgasnatural\.pt/authorizationserver/oauth/authorize\?.*"
)
CSRF_URL = f"{AUTH_BASE_URL}{CSRF_PATH}"
LOGIN_URL = f"{AUTH_BASE_URL}{LOGIN_PATH}"
TOKEN_URL = f"{AUTH_BASE_URL}{TOKEN_PATH}"
OCC_ROOT = f"{OCC_BASE_URL}/occ/v2/{OCC_BASE_SITE}"
CLIENT_INFO_URL = re.compile(
    re.escape(f"{OCC_ROOT}/users/current/selfcare/clientInfo") + r"\?.*"
)
CONTRACT_INFO_URL = re.compile(
    re.escape(f"{OCC_ROOT}/users/current/selfcare/selfCareContractInfo") + r"\?.*"
)

CODE_REDIRECT = f"{OAUTH_REDIRECT_URI}?code=test-code&state=test-state"
TOKEN_RESPONSE = {
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "scope": "basic",
    "token_type": "Bearer",
    "expires_in": 43199,
}
CLIENT_INFO_RESPONSE = {
    "customerPk": "0000000000000",
    "email": "user@example.pt",
    "firstName": "Test",
    "lastName": "User",
    "associatedContracts": [
        {
            "guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "contractNumber": "34_00000000_00000000",
            "energyType": "GAS",
            "name": "R. EXAMPLE   1, 2, 3",
        }
    ],
}


def mock_successful_login(mocked: aioresponses, *, token=None) -> None:
    """Register the four responses a successful login walks through."""
    mocked.get(AUTHORIZE_URL, status=302, headers={"Location": OAUTH_REDIRECT_URI})
    mocked.get(CSRF_URL, payload={"parameterName": "_csrf", "token": "csrf-1"})
    mocked.post(LOGIN_URL, status=302, headers={"Location": CODE_REDIRECT})
    mocked.post(TOKEN_URL, payload=token or TOKEN_RESPONSE)


async def test_login_completes_the_pkce_flow():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        await client.async_login()
    await client.close()


async def test_login_sends_the_code_verifier_on_the_token_exchange():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        await client.async_login()

        token_calls = [
            call
            for key, calls in mocked.requests.items()
            for call in calls
            if str(key[1]).endswith(TOKEN_PATH)
        ]
        body = token_calls[0].kwargs["data"]

    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "test-code"
    # PKCE: a public client proves possession with the verifier, not a secret.
    assert body["code_verifier"]
    assert "client_secret" not in body
    await client.close()


async def test_bad_credentials_raise_auth_error():
    client = CurGasNaturalClient("user@example.pt", "wrong")
    with aioresponses() as mocked:
        mocked.get(AUTHORIZE_URL, status=302, headers={"Location": OAUTH_REDIRECT_URI})
        mocked.get(CSRF_URL, payload={"parameterName": "_csrf", "token": "csrf-1"})
        mocked.post(
            LOGIN_URL,
            status=302,
            headers={"Location": f"{OAUTH_REDIRECT_URI}?error=bad_credentials"},
        )

        with pytest.raises(CurGasNaturalAuthError, match="bad_credentials"):
            await client.async_login()
    await client.close()


async def test_login_without_a_code_raises_auth_error():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mocked.get(AUTHORIZE_URL, status=302, headers={"Location": OAUTH_REDIRECT_URI})
        mocked.get(CSRF_URL, payload={"parameterName": "_csrf", "token": "csrf-1"})
        # A 302 chain that never produces a code (e.g. bounced back to login).
        mocked.post(LOGIN_URL, status=302, headers={"Location": ""})

        with pytest.raises(CurGasNaturalAuthError, match="authorization code"):
            await client.async_login()
    await client.close()


async def test_authorize_step_failure_raises_auth_error():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mocked.get(AUTHORIZE_URL, status=400, body="no OAuth client")

        with pytest.raises(CurGasNaturalAuthError, match="HTTP 400"):
            await client.async_login()
    await client.close()


async def test_missing_csrf_token_raises_auth_error():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mocked.get(AUTHORIZE_URL, status=302, headers={"Location": OAUTH_REDIRECT_URI})
        mocked.get(CSRF_URL, payload={"error": "bad_request"})

        with pytest.raises(CurGasNaturalAuthError, match="CSRF"):
            await client.async_login()
    await client.close()


async def test_connection_failure_raises_connection_error():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mocked.get(AUTHORIZE_URL, exception=aiohttp.ClientError("network down"))

        with pytest.raises(CurGasNaturalConnectionError):
            await client.async_login()
    await client.close()


async def test_a_timeout_raises_connection_error():
    """A timeout is not an ``aiohttp.ClientError``, so it needs its own handling."""
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mocked.get(AUTHORIZE_URL, exception=TimeoutError)

        with pytest.raises(CurGasNaturalConnectionError):
            await client.async_login()
    await client.close()


async def test_a_timeout_mid_poll_raises_connection_error():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        mocked.get(CLIENT_INFO_URL, exception=TimeoutError)

        with pytest.raises(CurGasNaturalConnectionError):
            await client.async_list_contracts()
    await client.close()


async def test_list_contracts_returns_the_associated_contracts():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        mocked.get(CLIENT_INFO_URL, payload=CLIENT_INFO_RESPONSE)

        contracts = await client.async_list_contracts()

    assert len(contracts) == 1
    assert contracts[0]["contractNumber"] == "34_00000000_00000000"
    await client.close()


async def test_list_contracts_sends_the_bearer_token():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        mocked.get(CLIENT_INFO_URL, payload=CLIENT_INFO_RESPONSE)
        await client.async_list_contracts()

        occ_calls = [
            call
            for key, calls in mocked.requests.items()
            for call in calls
            if "clientInfo" in str(key[1])
        ]

    assert occ_calls[0].kwargs["headers"]["Authorization"] == "Bearer access-1"
    await client.close()


async def test_list_contracts_tolerates_a_payload_without_contracts():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        mocked.get(CLIENT_INFO_URL, payload={"email": "user@example.pt"})

        assert await client.async_list_contracts() == []
    await client.close()


async def test_a_valid_token_is_reused_instead_of_logging_in_again():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        mocked.get(CLIENT_INFO_URL, payload=CLIENT_INFO_RESPONSE)
        mocked.get(CLIENT_INFO_URL, payload=CLIENT_INFO_RESPONSE)

        await client.async_list_contracts()
        await client.async_list_contracts()

        logins = [
            call
            for key, calls in mocked.requests.items()
            for call in calls
            if str(key[1]).endswith(LOGIN_PATH)
        ]

    assert len(logins) == 1
    await client.close()


async def test_an_expired_token_is_renewed_with_the_refresh_grant():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        mocked.get(CLIENT_INFO_URL, payload=CLIENT_INFO_RESPONSE)
        await client.async_list_contracts()

        # Force the cached token past its deadline.
        client._expires_at = 0.0
        mocked.post(
            TOKEN_URL,
            payload={**TOKEN_RESPONSE, "access_token": "access-2"},
        )
        mocked.get(CLIENT_INFO_URL, payload=CLIENT_INFO_RESPONSE)
        await client.async_list_contracts()

        refreshes = [
            call
            for key, calls in mocked.requests.items()
            for call in calls
            if str(key[1]).endswith(TOKEN_PATH)
        ]
        logins = [
            call
            for key, calls in mocked.requests.items()
            for call in calls
            if str(key[1]).endswith(LOGIN_PATH)
        ]

    assert refreshes[-1].kwargs["data"]["grant_type"] == "refresh_token"
    # A refresh must not fall back to the full four-request dance.
    assert len(logins) == 1
    await client.close()


async def test_a_rejected_token_triggers_exactly_one_re_login():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        mocked.get(CLIENT_INFO_URL, status=401)
        mock_successful_login(mocked)
        mocked.get(CLIENT_INFO_URL, payload=CLIENT_INFO_RESPONSE)

        contracts = await client.async_list_contracts()

        logins = [
            call
            for key, calls in mocked.requests.items()
            for call in calls
            if str(key[1]).endswith(LOGIN_PATH)
        ]

    assert len(contracts) == 1
    assert len(logins) == 2
    await client.close()


async def test_a_persistently_rejected_token_gives_up():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        mocked.get(CLIENT_INFO_URL, status=401)
        mock_successful_login(mocked)
        mocked.get(CLIENT_INFO_URL, status=401)

        with pytest.raises(CurGasNaturalAuthError):
            await client.async_list_contracts()
    await client.close()


async def test_get_data_requests_the_expected_reading_window():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        for path in (
            "selfCareContractInfo",
            "getMeterRead",
            "getLastMeterRead",
            "invoices/last-invoice",
            "invoices",
        ):
            mocked.post(
                re.compile(
                    re.escape(f"{OCC_ROOT}/users/current/selfcare/")
                    + rf".*{re.escape(path)}\?.*"
                ),
                payload={},
            )

        raw = await client.async_get_data(
            "contract-1", today=date(2026, 7, 31), cui=None
        )

        reads = [
            call
            for key, calls in mocked.requests.items()
            for call in calls
            if "getMeterRead" in str(key[1])
        ]
        body = reads[0].kwargs["json"]

    # HISTORY_DAYS = 400 days back from the supplied "today".
    assert body["dateTo"] == "20260731"
    assert body["dateFrom"] == "20250626"
    assert body["energyType"] == "GAS"
    assert body["contractId"] == "contract-1"
    assert "contract" in raw
    await client.close()


async def test_get_data_skips_the_distributor_lookup_without_a_cui():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        mocked.post(
            re.compile(re.escape(f"{OCC_ROOT}/users/current/selfcare/") + r".*"),
            payload={},
            repeat=True,
        )

        raw = await client.async_get_data(
            "contract-1", today=date(2026, 7, 31), cui=None
        )

    assert "distributor" not in raw
    await client.close()


async def test_get_contract_info_returns_a_dict_even_on_a_odd_payload():
    client = CurGasNaturalClient("user@example.pt", "secret")
    with aioresponses() as mocked:
        mock_successful_login(mocked)
        mocked.post(CONTRACT_INFO_URL, payload=["unexpected"])

        assert await client.async_get_contract_info("contract-1") == {}
    await client.close()
