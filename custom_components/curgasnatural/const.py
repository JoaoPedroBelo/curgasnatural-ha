"""Constants for the CUR Gás Natural integration."""

from typing import Final

DOMAIN: Final = "curgasnatural"

# --- Configuration keys ---
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
# The contract this entry tracks. A CUR account can hold several contracts (one
# per delivery point), so each is configured as its own entry and identified by
# the ``guid`` returned by ``clientInfo``.
CONF_CONTRACT_ID: Final = "contract_id"
CONF_CONTRACT_NUMBER: Final = "contract_number"
CONF_CUI: Final = "cui"
CONF_ADDRESS: Final = "address"

# --- Volume to energy conversion ---
# The meter counts m³ but the supplier bills kWh, using the PCS of the
# distribution network printed on every invoice next to the reading
# ("4 m³ x 11.20808 = 45 kWh"). It is network-specific and revised occasionally,
# so it is an editable option rather than a constant, and no endpoint on this
# portal exposes it (see docs/API.md).
CONF_CONVERSION_FACTOR: Final = "conversion_factor"
DEFAULT_CONVERSION_FACTOR: Final = 11.2
# Guard rails for the options form. Portuguese networks sit around 11-12 kWh/m³;
# anything outside this is a typo, and a wrong factor silently distorts both the
# energy sensors and the Gas dashboard's cost.
MIN_CONVERSION_FACTOR: Final = 8.0
MAX_CONVERSION_FACTOR: Final = 14.0

# --- Endpoints (verified live; see docs/API.md) ---
# Auth is a separate host from the data API: the OAuth2 authorization server sits
# behind the portal domain, the OCC REST API on SAP Commerce Cloud.
AUTH_BASE_URL: Final = "https://api-portal.curgasnatural.pt"
PORTAL_ORIGIN: Final = "https://portal.curgasnatural.pt"
OCC_BASE_URL: Final = (
    "https://api.cfl9u3by7k-galpenerg3-p1-public.model-t.cc.commerce.ondemand.com"
)
OCC_BASE_SITE: Final = "galpcurarea"

# OAuth2 authorization-code + PKCE. A public client: there is no client secret,
# the ``code_verifier`` is what proves possession of the authorization code.
OAUTH_CLIENT_ID: Final = "galpCURUserClientLogin"
OAUTH_REDIRECT_URI: Final = f"{PORTAL_ORIGIN}/area-privada/iniciar-sessao"
OAUTH_SCOPE: Final = "basic"

CSRF_PATH: Final = "/authorizationserver/csrf"
LOGIN_PATH: Final = "/authorizationserver/login"
AUTHORIZE_PATH: Final = "/authorizationserver/oauth/authorize"
TOKEN_PATH: Final = "/authorizationserver/oauth/token"

# Self-care endpoints, relative to ``/occ/v2/<base site>``.
EP_CLIENT_INFO: Final = "/users/current/selfcare/clientInfo"
EP_CONTRACT_INFO: Final = "/users/current/selfcare/selfCareContractInfo"
EP_LAST_METER_READ: Final = "/users/current/selfcare/getLastMeterRead"
EP_METER_READ: Final = "/users/current/selfcare/getMeterRead"
EP_CUI_INFO: Final = "/users/current/selfcare/cur/cuiInfo"
EP_INVOICES: Final = "/users/current/selfcare/contract/invoices"
EP_LAST_INVOICE: Final = "/users/current/selfcare/contract/invoices/last-invoice"

# Only gas is sold under the CUR regime this portal serves.
ENERGY_TYPE_GAS: Final = "GAS"

# How far back the meter-reading history is requested. Readings are sparse (the
# distributor reads roughly monthly), so a wide window costs one request and
# gives the statistics importer a full year of history on a fresh install.
HISTORY_DAYS: Final = 400
# ``getLastMeterRead`` also carries the *next* reading window, which only makes
# sense against a recent range.
RECENT_DAYS: Final = 90

# Redirect hops followed while completing the login. The verified chain is
# login -> authorize(continue) -> redirect_uri?code, so 5 is generous.
MAX_AUTH_REDIRECTS: Final = 5

# --- Polling schedule ---
# Meter readings are backdated and sparse — the distributor reads roughly
# monthly, and a client-submitted reading lands whenever the user files it. There
# is nothing to gain from a tight loop, so we poll twice a day at fixed hours
# (02:00 / 14:00 local) to stay low-profile against the portal.
POLL_HOURS: Final = (2, 14)
POLL_MINUTE: Final = 0

# --- Entity unique ID suffixes ---
SENSOR_METER_INDEX: Final = "meter_index"  # cumulative meter reading (m³)
SENSOR_METER_INDEX_ENERGY: Final = "meter_index_energy"  # same reading in kWh
SENSOR_LAST_CONSUMPTION: Final = "last_consumption"  # m³ between last 2 readings
SENSOR_LAST_CONSUMPTION_ENERGY: Final = "last_consumption_energy"  # same in kWh
SENSOR_LAST_READING_DATE: Final = "last_reading_date"
SENSOR_DAYS_SINCE_READING: Final = "days_since_reading"
SENSOR_NEXT_READING_START: Final = "next_reading_start"
SENSOR_NEXT_READING_END: Final = "next_reading_end"
SENSOR_LAST_INVOICE_TOTAL: Final = "last_invoice_total"
SENSOR_LAST_INVOICE_DUE: Final = "last_invoice_due"
SENSOR_AMOUNT_DUE: Final = "amount_due"
SENSOR_BILLED_12M: Final = "billed_12m"

BINARY_SENSOR_AVAILABLE: Final = "available"  # portal reachable
BINARY_SENSOR_INVOICE_PENDING: Final = "invoice_pending"
BINARY_SENSOR_DIRECT_DEBIT_FAILED: Final = "direct_debit_failed"

# --- coordinator.data keys ---
DATA_AVAILABLE: Final = "available"
# [{"iso": "2026-07-30", "index": 320.0, "origin": "Leitura do cliente"}], oldest
# first. ``index`` is the cumulative meter reading in m³.
DATA_READINGS: Final = "readings"
DATA_METER_INDEX: Final = "meter_index"
DATA_METER_INDEX_ENERGY: Final = "meter_index_energy"  # index x factor (kWh)
DATA_CONVERSION_FACTOR: Final = "conversion_factor"  # kWh/m³ in force
DATA_LAST_READING_ISO: Final = "last_reading_iso"
DATA_LAST_READING_ORIGIN: Final = "last_reading_origin"
DATA_LAST_CONSUMPTION: Final = "last_consumption"
DATA_LAST_CONSUMPTION_ENERGY: Final = "last_consumption_energy"  # kWh
DATA_LAST_CONSUMPTION_DAYS: Final = "last_consumption_days"
DATA_LAST_CONSUMPTION_FROM: Final = "last_consumption_from"
DATA_NEXT_READING_START: Final = "next_reading_start"
DATA_NEXT_READING_END: Final = "next_reading_end"
DATA_DELIVERY_POINT: Final = "delivery_point"
DATA_DIVISION_TEXT: Final = "division_text"

DATA_CONTRACT_STATUS: Final = "contract_status"
DATA_CONTRACT_NUMBER: Final = "contract_number"
DATA_CONTRACT_TIER: Final = "contract_tier"
DATA_DIRECT_DEBIT: Final = "direct_debit"
DATA_DISTRIBUTOR: Final = "distributor"

DATA_LAST_INVOICE_TOTAL: Final = "last_invoice_total"
DATA_LAST_INVOICE_DUE: Final = "last_invoice_due"
DATA_LAST_INVOICE_EMITTED: Final = "last_invoice_emitted"
DATA_LAST_INVOICE_PERIOD_START: Final = "last_invoice_period_start"
DATA_LAST_INVOICE_PERIOD_END: Final = "last_invoice_period_end"
DATA_LAST_INVOICE_NUMBER: Final = "last_invoice_number"
DATA_LAST_INVOICE_STATUS: Final = "last_invoice_status"
DATA_AMOUNT_DUE: Final = "amount_due"
DATA_INVOICE_PENDING: Final = "invoice_pending"
DATA_DIRECT_DEBIT_FAILED: Final = "direct_debit_failed"
DATA_BILLED_12M: Final = "billed_12m"
# [{"iso": "2026-07-20", "total": 12.34}], oldest first: one entry per issued
# invoice, keyed by the end of the period it bills. Feeds the cost statistic.
DATA_INVOICE_SERIES: Final = "invoice_series"

# --- Attributes ---
ATTR_CUI: Final = "cui"
ATTR_CONTRACT_NUMBER: Final = "contract_number"
ATTR_ORIGIN: Final = "origin"
ATTR_READING_DATE: Final = "reading_date"
ATTR_PERIOD_START: Final = "period_start"
ATTR_PERIOD_END: Final = "period_end"
ATTR_DAYS: Final = "days"
ATTR_INVOICE_NUMBER: Final = "invoice_number"
ATTR_PAYMENT_STATUS: Final = "payment_status"
ATTR_DISTRIBUTOR: Final = "distributor"
ATTR_TIER: Final = "tier"
ATTR_CONVERSION_FACTOR: Final = "conversion_factor"

# Invoice payment statuses seen live.
INVOICE_STATUS_PAID: Final = "PAID"
INVOICE_STATUS_PENDING: Final = "PENDING_PAYMENT"
