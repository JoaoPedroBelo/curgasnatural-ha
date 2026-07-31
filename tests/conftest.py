"""Fixtures for CUR Gás Natural tests.

The payloads mirror the shapes captured live from the portal (see
``custom_components/curgasnatural/docs/API.md``), with every identifier replaced
by a placeholder — no real CUI, contract number, NIF or address.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.curgasnatural.const import (
    CONF_ADDRESS,
    CONF_CONTRACT_ID,
    CONF_CONTRACT_NUMBER,
    CONF_CUI,
    CONF_EMAIL,
    CONF_PASSWORD,
)


@pytest.fixture(autouse=True, scope="session")
def _start_dns_resolver_thread() -> None:
    """Let pycares start its permanent daemon thread before tests are watched.

    aiohttp resolves DNS through aiodns/pycares, and pycares destroys resolver
    channels on a single module-level daemon thread that it starts lazily and never
    joins — by design, see ``pycares._ChannelShutdownManager``.

    ``pytest-homeassistant-custom-component``'s cleanup check diffs the thread list
    around every test, so whichever test first builds an ``aiohttp.ClientSession``
    gets blamed for that thread and errors at teardown. Building and closing one
    session here, before any test is observed, keeps the diff honest.

    Symptom if this is removed: `AssertionError` at the teardown of the first
    api test, naming `Thread-1 (_run_safe_shutdown_loop)`.
    """
    loop = asyncio.new_event_loop()
    try:
        session = loop.run_until_complete(_open_session())
        loop.run_until_complete(session.close())
    finally:
        loop.close()


async def _open_session() -> aiohttp.ClientSession:
    """Build a session on the running loop, as aiohttp requires."""
    return aiohttp.ClientSession()


TEST_CONTRACT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TEST_CONTRACT_NUMBER = "34_00000000_00000000"
TEST_CUI = "PT1605000000000000XX"
TEST_ADDRESS = "R. EXAMPLE 1, 2, 3"
TEST_EMAIL = "user@example.pt"


@pytest.fixture
def raw_payload():
    """A full ``client.async_get_data`` payload, shaped like the live one."""
    return {
        "contract": {
            "CUI": TEST_CUI,
            "agreementStatus": "ACTIVE",
            "agreementType": "CUR",
            "agreement_id": TEST_CONTRACT_ID,
            "contractNumber": TEST_CONTRACT_NUMBER,
            "directDebit": True,
            "energyType": "GAS",
            "tier": "ESCALAO_1",
        },
        "readings": {
            "deliveryPoint": TEST_CUI,
            "divisionText": "Mercado Regulado",
            "readings": [
                {
                    "date": "21-10-2025",
                    "gv": "100",
                    "originText": "Leitura do distribuidor",
                },
                {
                    "date": "13-04-2026",
                    "gv": "200",
                    "originText": "Leitura do distribuidor",
                },
                {
                    "date": "11-07-2026",
                    "gv": "300",
                    "originText": "Leitura do cliente",
                },
                # Same day reported twice: keep the higher index.
                {
                    "date": "30-07-2026",
                    "gv": "310",
                    "originText": "Leitura do distribuidor",
                },
                {
                    "date": "30-07-2026",
                    "gv": "320",
                    "originText": "Leitura do cliente",
                },
            ],
        },
        "last_reading": {
            "deliveryPoint": TEST_CUI,
            "divisionText": "Mercado Regulado",
            "idealPeriodBegin": "20260820",
            "idealPeriodEnd": "20260823",
            "readings": [
                {
                    "date": "30-07-2026",
                    "gv": "320",
                    "maximumValue": "340",
                    "minimumValue": "320 ",
                    "originText": "Leitura do cliente",
                }
            ],
        },
        "last_invoice": {
            "lastInvoice": {
                "documentFiscalNumber": ["FT K0000/00000000000"],
                "dueDate": "2026-08-17T00:00:00+0000",
                "emissionDate": "2026-07-23T00:00:00+0000",
                "endBillingPeriod": "2026-07-20T00:00:00+0000",
                "failedDirectDebit": False,
                "hasDirectDebit": True,
                "paymentStatus": "PENDING_PAYMENT",
                "startBillingPeriod": "2026-06-21T00:00:00+0000",
                "toPayValue": "12.34",
                "totalValue": "12.34",
            }
        },
        "invoices": {
            "invoices": [
                {
                    "dueDate": "2026-08-17T00:00:00+0000",
                    "emissionDate": "2026-07-23T00:00:00+0000",
                    "startBillingPeriod": "2026-06-21T00:00:00+0000",
                    "endBillingPeriod": "2026-07-20T00:00:00+0000",
                    "failedDirectDebit": False,
                    "paymentStatus": "PENDING_PAYMENT",
                    "totalValue": "12.34",
                },
                {
                    "dueDate": "2026-07-15T00:00:00+0000",
                    "emissionDate": "2026-06-20T00:00:00+0000",
                    "startBillingPeriod": "2026-05-17T00:00:00+0000",
                    "endBillingPeriod": "2026-06-20T00:00:00+0000",
                    "failedDirectDebit": False,
                    "paymentStatus": "PAID",
                    "totalValue": "25.67",
                },
                {
                    # Older than the 12-month window used by billed_12m.
                    "dueDate": "2024-07-15T00:00:00+0000",
                    "emissionDate": "2024-06-20T00:00:00+0000",
                    "startBillingPeriod": "2024-05-21T00:00:00+0000",
                    "endBillingPeriod": "2024-06-20T00:00:00+0000",
                    "failedDirectDebit": False,
                    "paymentStatus": "PAID",
                    "totalValue": "99.99",
                },
            ]
        },
        "distributor": {
            "address": "Avenida Example - Nº 8 - 1349-065 Lisboa",
            "description": "M.C.R.C de Lisboa",
            "phone": "211 000 000",
            "title": "Lisboagás Comercialização, S.A.",
        },
    }


@pytest.fixture
def mock_coordinator():
    """Mock CurGasNaturalCoordinator with normalised data."""
    coordinator = MagicMock()
    coordinator.data = {
        "available": True,
        "readings": [
            {"iso": "2026-07-11", "index": 300.0, "origin": "Leitura do cliente"},
            {"iso": "2026-07-30", "index": 320.0, "origin": "Leitura do cliente"},
        ],
        "meter_index": 320.0,
        "last_reading_iso": "2026-07-30",
        "last_reading_origin": "Leitura do cliente",
        "last_consumption": 20.0,
        "last_consumption_from": "2026-07-11",
        "last_consumption_days": 19,
        "next_reading_start": None,
        "next_reading_end": None,
        "contract_status": "ACTIVE",
        "contract_number": TEST_CONTRACT_NUMBER,
        "contract_tier": "ESCALAO_1",
        "delivery_point": TEST_CUI,
        "distributor": "Lisboagás Comercialização, S.A.",
        "last_invoice_total": 12.34,
        "last_invoice_status": "PENDING_PAYMENT",
        "amount_due": 12.34,
        "billed_12m": 38.01,
        "invoice_pending": True,
        "direct_debit_failed": False,
    }
    coordinator.contract_id = TEST_CONTRACT_ID
    coordinator.contract_number = TEST_CONTRACT_NUMBER
    coordinator.cui = TEST_CUI
    coordinator.last_update_success = True
    coordinator.client = MagicMock()
    coordinator.client.close = AsyncMock()
    return coordinator


@pytest.fixture
def mock_config_entry():
    """Mock ConfigEntry for one contract."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        CONF_EMAIL: TEST_EMAIL,
        CONF_PASSWORD: "secret",
        CONF_CONTRACT_ID: TEST_CONTRACT_ID,
        CONF_CONTRACT_NUMBER: TEST_CONTRACT_NUMBER,
        CONF_CUI: TEST_CUI,
        CONF_ADDRESS: TEST_ADDRESS,
    }
    entry.options = {}
    return entry
