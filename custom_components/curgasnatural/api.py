"""HTTP client for the CUR Gás Natural customer portal.

The portal at https://portal.curgasnatural.pt is an Angular (SAP Spartacus)
front end over **SAP Commerce Cloud**, so unlike a scraped web app it exposes a
typed JSON API — the OCC v2 REST API — behind an OAuth2 authorization server.
This client drives exactly the requests the portal's own front end makes.

Auth model (OAuth2 authorization code + PKCE, public client — no secret)::

    1. GET  /authorizationserver/oauth/authorize?...code_challenge...
           -> 302 to the portal's login page. This is what *creates* the
              server-side OAuth session; skipping it makes step 2 answer
              400 "No OAuth client related to request".
    2. GET  /authorizationserver/csrf   (Referer/Origin must be the portal)
           -> {"parameterName": "_csrf", "token": ...}
    3. POST /authorizationserver/login  (username, password, _csrf)
           -> 302 -> authorize(continue) -> 302 -> redirect_uri?code=...
              Bad credentials instead redirect to ...?error=bad_credentials.
    4. POST /authorizationserver/oauth/token (authorization_code, code_verifier)
           -> {"access_token", "refresh_token", "expires_in": 43199, ...}

The access token lasts ~12 h and the refresh grant works, so a poll normally
costs one refresh rather than the full four-request dance.

The cookie jar is owned by this client (never the shared HA session) so the
authenticated session — and the OAuth handshake's intermediate cookies — stay
isolated to this integration.

See docs/API.md for the captured requests this client is based on.
"""

from __future__ import annotations

import base64
from datetime import date, timedelta
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from .const import (
    AUTH_BASE_URL,
    AUTHORIZE_PATH,
    CSRF_PATH,
    ENERGY_TYPE_GAS,
    EP_CLIENT_INFO,
    EP_CONTRACT_INFO,
    EP_CUI_INFO,
    EP_INVOICES,
    EP_LAST_INVOICE,
    EP_LAST_METER_READ,
    EP_METER_READ,
    HISTORY_DAYS,
    LOGIN_PATH,
    MAX_AUTH_REDIRECTS,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPE,
    OCC_BASE_SITE,
    OCC_BASE_URL,
    PORTAL_ORIGIN,
    RECENT_DAYS,
    TOKEN_PATH,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds

# Renew the access token this many seconds before it actually expires, so a poll
# never races the expiry.
TOKEN_EXPIRY_MARGIN = 300

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
_DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"

# Header sets mirroring what a browser actually sends at each step. This is not
# cosmetic: the authorization server's CORS filter rejects the *next* request
# with 403 "Invalid CORS request" if ``Origin`` is sent on the /authorize
# navigation (verified live), and answers /csrf with 400 "Login page
# configuration does not match the request" if ``Referer`` is *missing* there.
#
# /authorize and the redirect hops are top-level navigations -> no Origin/Referer.
_NAV_HEADERS: dict[str, str] = {}
# /csrf is an XHR from the portal page -> Origin + Referer required.
_XHR_HEADERS = {
    "Origin": PORTAL_ORIGIN,
    "Referer": OAUTH_REDIRECT_URI,
    "Accept": "application/json, text/plain, */*",
}
# /login is a form submit -> Origin, but no Referer.
_FORM_HEADERS = {
    "Content-Type": _FORM_CONTENT_TYPE,
    "Origin": PORTAL_ORIGIN,
}

# Query parameters the portal sends on every OCC call.
_OCC_PARAMS = {"lang": "pt", "curr": "EUR"}


def _b64url(raw: bytes) -> str:
    """Base64url-encode without padding, as PKCE requires."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    """Return a fresh ``(code_verifier, code_challenge)`` PKCE pair (S256)."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


class CurGasNaturalError(Exception):
    """Base error for the CUR Gás Natural client."""


class CurGasNaturalAuthError(CurGasNaturalError):
    """Raised when authentication fails (bad credentials or an expired session)."""


class CurGasNaturalConnectionError(CurGasNaturalError):
    """Raised when the portal cannot be reached."""


class CurGasNaturalClient:
    """Client for the CUR Gás Natural portal's OAuth2 + OCC v2 API."""

    def __init__(self, email: str, password: str) -> None:
        """Store credentials. The session is created lazily on first use."""
        self._email = email
        self._password = password
        self._session: aiohttp.ClientSession | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        # ``time.monotonic()`` deadline, immune to wall-clock adjustments.
        self._expires_at = 0.0

    # --- session lifecycle -------------------------------------------------

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Create the private cookie-backed session on first use."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=_DEFAULT_HEADERS,
                cookie_jar=aiohttp.CookieJar(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying session (call on unload)."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._forget_token()

    def _forget_token(self) -> None:
        """Drop the cached tokens so the next call re-authenticates."""
        self._access_token = None
        self._refresh_token = None
        self._expires_at = 0.0

    # --- authentication ----------------------------------------------------

    async def async_login(self) -> None:
        """Run the full authorization-code + PKCE flow. Raises on failure."""
        session = await self._ensure_session()
        # The handshake is stateful server-side; stale cookies from a previous
        # attempt make the authorization server resume the wrong OAuth session.
        session.cookie_jar.clear()
        self._forget_token()

        verifier, challenge = _pkce_pair()
        state = _b64url(secrets.token_bytes(16))

        await self._start_authorization(challenge, state)
        csrf_field, csrf_token = await self._fetch_csrf()
        code = await self._submit_login(csrf_field, csrf_token)
        await self._exchange_code(code, verifier)
        # Deliberately without the e-mail: debug logs get pasted into bug reports.
        _LOGGER.debug("CUR Gás Natural login successful")

    async def _start_authorization(self, challenge: str, state: str) -> None:
        """Open the authorization request so the server saves it and sets cookies.

        The response is the 302 to the portal's login page; we only need the
        session it establishes, not the body.
        """
        query = urlencode(
            {
                "response_type": "code",
                "client_id": OAUTH_CLIENT_ID,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "scope": OAUTH_SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        url = f"{AUTH_BASE_URL}{AUTHORIZE_PATH}?{query}"
        session = await self._ensure_session()
        try:
            async with session.get(
                url, allow_redirects=False, headers=_NAV_HEADERS
            ) as resp:
                if resp.status not in {302, 303}:
                    body = await resp.text()
                    raise CurGasNaturalAuthError(
                        f"Authorization request returned HTTP {resp.status}: "
                        f"{body[:200]}"
                    )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CurGasNaturalConnectionError(
                f"Authorization request failed: {err}"
            ) from err

    async def _fetch_csrf(self) -> tuple[str, str]:
        """Return the ``(field name, token)`` for the login form's CSRF guard."""
        session = await self._ensure_session()
        url = f"{AUTH_BASE_URL}{CSRF_PATH}"
        try:
            async with session.get(url, headers=_XHR_HEADERS) as resp:
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CurGasNaturalConnectionError(f"CSRF request failed: {err}") from err

        if not isinstance(payload, dict) or not payload.get("token"):
            raise CurGasNaturalAuthError(f"No CSRF token in response: {payload}")
        return str(payload.get("parameterName") or "_csrf"), str(payload["token"])

    async def _submit_login(self, csrf_field: str, csrf_token: str) -> str:
        """POST the credentials and follow the redirects to the authorization code.

        The verified chain is ``login -> authorize(continue) -> redirect_uri?code``.
        Wrong credentials short-circuit it with ``?error=bad_credentials`` instead.
        """
        session = await self._ensure_session()
        url = f"{AUTH_BASE_URL}{LOGIN_PATH}"
        payload = {
            "username": self._email,
            "password": self._password,
            csrf_field: csrf_token,
        }
        try:
            async with session.post(
                url,
                data=payload,
                allow_redirects=False,
                headers=_FORM_HEADERS,
            ) as resp:
                location = resp.headers.get("Location", "")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CurGasNaturalConnectionError(f"Login request failed: {err}") from err

        return await self._follow_to_code(location)

    async def _follow_to_code(self, location: str) -> str:
        """Walk the redirect chain until the authorization code appears."""
        session = await self._ensure_session()
        for _ in range(MAX_AUTH_REDIRECTS):
            if not location:
                break
            query = parse_qs(urlparse(location).query)
            if "error" in query:
                # ``bad_credentials`` is the only error seen live, but any error
                # here is an auth problem rather than a transport one.
                raise CurGasNaturalAuthError(f"Login rejected: {query['error'][0]}")
            if "code" in query:
                return str(query["code"][0])
            try:
                async with session.get(
                    location, allow_redirects=False, headers=_NAV_HEADERS
                ) as resp:
                    location = resp.headers.get("Location", "")
            except (aiohttp.ClientError, TimeoutError) as err:
                raise CurGasNaturalConnectionError(
                    f"Following the login redirect failed: {err}"
                ) from err

        raise CurGasNaturalAuthError(
            "Login did not produce an authorization code - check email and password"
        )

    async def _exchange_code(self, code: str, verifier: str) -> None:
        """Exchange the authorization code for tokens (PKCE, no client secret)."""
        await self._post_token(
            {
                "grant_type": "authorization_code",
                "client_id": OAUTH_CLIENT_ID,
                "code": code,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "code_verifier": verifier,
            }
        )

    async def _refresh_access_token(self) -> None:
        """Renew the access token with the refresh grant."""
        if not self._refresh_token:
            raise CurGasNaturalAuthError("No refresh token available")
        await self._post_token(
            {
                "grant_type": "refresh_token",
                "client_id": OAUTH_CLIENT_ID,
                "refresh_token": self._refresh_token,
            }
        )

    async def _post_token(self, payload: dict[str, str]) -> None:
        """POST the token endpoint and store the resulting tokens."""
        session = await self._ensure_session()
        url = f"{AUTH_BASE_URL}{TOKEN_PATH}"
        try:
            async with session.post(
                url,
                data=payload,
                headers=_FORM_HEADERS,
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status >= 400:
                    raise CurGasNaturalAuthError(
                        f"Token endpoint returned HTTP {resp.status}: {body}"
                    )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CurGasNaturalConnectionError(f"Token request failed: {err}") from err

        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise CurGasNaturalAuthError(f"No access token in response: {body}")

        self._access_token = str(token)
        # A refresh response may omit the refresh token; keep the previous one.
        self._refresh_token = str(
            body.get("refresh_token") or self._refresh_token or ""
        )
        expires_in = body.get("expires_in") or 0
        try:
            lifetime = float(expires_in)
        except (TypeError, ValueError):
            lifetime = 0.0
        self._expires_at = time.monotonic() + max(lifetime - TOKEN_EXPIRY_MARGIN, 0.0)

    async def _ensure_token(self) -> str:
        """Return a usable access token, refreshing or logging in as needed."""
        if self._access_token and time.monotonic() < self._expires_at:
            return self._access_token

        if self._refresh_token:
            try:
                await self._refresh_access_token()
            except CurGasNaturalAuthError as err:
                _LOGGER.debug("Refresh token rejected (%s); logging in again", err)
            else:
                return self._access_token or ""

        await self.async_login()
        return self._access_token or ""

    # --- OCC requests ------------------------------------------------------

    async def _occ_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_retry: bool = True,
    ) -> Any:
        """Call an OCC endpoint with a bearer token and return the parsed JSON.

        A 401/403 means the token went stale despite our bookkeeping, so the
        token is dropped and the call retried once with a fresh login.
        """
        token = await self._ensure_token()
        session = await self._ensure_session()
        url = f"{OCC_BASE_URL}/occ/v2/{OCC_BASE_SITE}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/plain, */*",
            "Origin": PORTAL_ORIGIN,
            "Referer": f"{PORTAL_ORIGIN}/",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with session.request(
                method,
                url,
                params={**_OCC_PARAMS, **(params or {})},
                json=json_body,
                headers=headers,
            ) as resp:
                if resp.status in {401, 403}:
                    raise CurGasNaturalAuthError(f"{path} returned {resp.status}")
                if resp.status >= 400:
                    body = await resp.text()
                    raise CurGasNaturalError(
                        f"{path} returned HTTP {resp.status}: {body[:200]}"
                    )
                return await resp.json(content_type=None)
        except CurGasNaturalAuthError:
            if not allow_retry:
                raise
            _LOGGER.debug("Token rejected on %s; re-authenticating once", path)
            self._forget_token()
            return await self._occ_request(
                method,
                path,
                params=params,
                json_body=json_body,
                allow_retry=False,
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CurGasNaturalConnectionError(f"{path} request failed: {err}") from err

    # --- public API --------------------------------------------------------

    async def async_list_contracts(self) -> list[dict[str, Any]]:
        """Return the account's contracts, as the config flow's picker needs them.

        Shape (verified)::

            [{"guid": "...", "contractNumber": "34_...", "energyType": "GAS",
              "name": "R. EXAMPLE 1, 2, 3", "address": {...}}]
        """
        payload = await self._occ_request("GET", EP_CLIENT_INFO)
        contracts = (
            payload.get("associatedContracts") if isinstance(payload, dict) else None
        )
        if not isinstance(contracts, list):
            return []
        return [c for c in contracts if isinstance(c, dict)]

    async def async_get_contract_info(self, contract_id: str) -> dict[str, Any]:
        """Return one contract's details (CUI, status, tier, billing, SEPA).

        The config flow needs this to record the delivery point (CUI) alongside
        the contract, which ``clientInfo`` does not carry.
        """
        payload = await self._occ_request(
            "POST", EP_CONTRACT_INFO, json_body={"contractId": contract_id}
        )
        return payload if isinstance(payload, dict) else {}

    async def async_get_data(
        self, contract_id: str, *, today: date, cui: str | None = None
    ) -> dict[str, Any]:
        """Fetch every payload the integration needs for one contract.

        ``today`` is passed in (rather than read here) so the caller controls the
        timezone the reading window is expressed in, and so tests are stable.
        ``cui`` skips a round trip when the delivery point is already known.
        """
        body = {"contractId": contract_id}
        reading_body = {
            "dateFrom": (today - timedelta(days=HISTORY_DAYS)).strftime("%Y%m%d"),
            "dateTo": today.strftime("%Y%m%d"),
            "contractId": contract_id,
            "energyType": ENERGY_TYPE_GAS,
        }
        recent_body = {
            **reading_body,
            "dateFrom": (today - timedelta(days=RECENT_DAYS)).strftime("%Y%m%d"),
        }

        contract = await self._occ_request("POST", EP_CONTRACT_INFO, json_body=body)
        raw: dict[str, Any] = {
            "contract": contract,
            # ``getMeterRead`` answers 201 with the full reading history;
            # ``getLastMeterRead`` adds the next expected reading window.
            "readings": await self._occ_request(
                "POST", EP_METER_READ, json_body=reading_body
            ),
            "last_reading": await self._occ_request(
                "POST", EP_LAST_METER_READ, json_body=recent_body
            ),
            "last_invoice": await self._occ_request(
                "POST", EP_LAST_INVOICE, json_body=body
            ),
            "invoices": await self._occ_request("POST", EP_INVOICES, json_body=body),
        }

        delivery_point = cui or (
            contract.get("CUI") if isinstance(contract, dict) else None
        )
        if delivery_point:
            raw["distributor"] = await self._occ_request(
                "GET", EP_CUI_INFO, params={"cui": str(delivery_point)}
            )
        return raw
