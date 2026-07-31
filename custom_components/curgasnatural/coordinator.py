"""Data update coordinator for the CUR Gás Natural integration."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CurGasNaturalAuthError, CurGasNaturalClient, CurGasNaturalError
from .const import (
    CONF_CONTRACT_ID,
    CONF_CONTRACT_NUMBER,
    CONF_CONVERSION_FACTOR,
    CONF_CUI,
    CONF_EMAIL,
    CONF_PASSWORD,
    DATA_AMOUNT_DUE,
    DATA_AVAILABLE,
    DATA_BILLED_12M,
    DATA_CONTRACT_NUMBER,
    DATA_CONTRACT_STATUS,
    DATA_CONTRACT_TIER,
    DATA_CONVERSION_FACTOR,
    DATA_DELIVERY_POINT,
    DATA_DIRECT_DEBIT,
    DATA_DIRECT_DEBIT_FAILED,
    DATA_DISTRIBUTOR,
    DATA_DIVISION_TEXT,
    DATA_INVOICE_PENDING,
    DATA_INVOICE_SERIES,
    DATA_LAST_CONSUMPTION,
    DATA_LAST_CONSUMPTION_DAYS,
    DATA_LAST_CONSUMPTION_ENERGY,
    DATA_LAST_CONSUMPTION_FROM,
    DATA_LAST_INVOICE_DUE,
    DATA_LAST_INVOICE_EMITTED,
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
    DATA_READINGS,
    DEFAULT_CONVERSION_FACTOR,
    DOMAIN,
    INVOICE_STATUS_PENDING,
    POLL_HOURS,
    POLL_MINUTE,
)
from .statistics import async_import_consumption_statistics

_LOGGER = logging.getLogger(__name__)

# Window used for the "billed in the last 12 months" total.
BILLING_YEAR_DAYS = 365


class CurGasNaturalCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the CUR portal and expose normalised data to entities.

    All entity state lives here in ``self.data``; entities only ever read
    ``self.data.get(...)`` and return ``None`` for missing values.
    """

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialise the coordinator from the merged config entry data."""
        self.contract_id: str = config[CONF_CONTRACT_ID]
        self.contract_number: str = config[CONF_CONTRACT_NUMBER]
        self.cui: str | None = config.get(CONF_CUI)
        # kWh/m³ used to bill this delivery point (see CONF_CONVERSION_FACTOR).
        self.conversion_factor: float = _to_positive_float(
            config.get(CONF_CONVERSION_FACTOR), DEFAULT_CONVERSION_FACTOR
        )

        self.client = CurGasNaturalClient(
            email=config[CONF_EMAIL],
            password=config[CONF_PASSWORD],
        )
        self._unsub_schedule: list[CALLBACK_TYPE] = []

        # No periodic ``update_interval``: we poll on a fixed twice-daily
        # schedule instead (see ``async_setup_schedule``) to stay low-profile.
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)

    @callback
    def async_setup_schedule(self) -> None:
        """Register the fixed twice-daily refreshes (see ``POLL_HOURS``)."""

        async def _scheduled_refresh(_now: Any) -> None:
            await self.async_request_refresh()

        for hour in POLL_HOURS:
            self._unsub_schedule.append(
                async_track_time_change(
                    self.hass,
                    _scheduled_refresh,
                    hour=hour,
                    minute=POLL_MINUTE,
                    second=0,
                )
            )

    def async_teardown_schedule(self) -> None:
        """Cancel all scheduled refreshes."""
        while self._unsub_schedule:
            self._unsub_schedule.pop()()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and normalise the latest data for this contract."""
        try:
            raw = await self.client.async_get_data(
                self.contract_id, today=dt_util.now().date(), cui=self.cui
            )
        except CurGasNaturalAuthError as err:
            # Auth errors are not transient - surface as a reauth so HA prompts
            # the user rather than retrying forever.
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except CurGasNaturalError as err:
            raise UpdateFailed(
                f"Error communicating with the CUR portal: {err}"
            ) from err

        data = self._normalise(raw, dt_util.now().date(), self.conversion_factor)
        await self._async_import_statistics(
            data.get(DATA_READINGS) or [], data.get(DATA_INVOICE_SERIES) or []
        )
        return data

    async def _async_import_statistics(
        self, readings: list[dict[str, Any]], invoices: list[dict[str, Any]]
    ) -> None:
        """Feed meter readings into the Gas dashboard's long-term statistics.

        A statistics hiccup (recorder not ready, etc.) must never fail the poll,
        so failures are logged and swallowed — the sensors still update.
        """
        if not readings:
            return
        try:
            await async_import_consumption_statistics(
                self.hass,
                self.contract_number,
                readings,
                self.conversion_factor,
                invoices,
            )
        except Exception:  # a stats failure must never fail the poll
            _LOGGER.warning(
                "Failed to import CUR consumption statistics", exc_info=True
            )

    @classmethod
    def _normalise(
        cls,
        raw: dict[str, Any],
        today: date,
        conversion_factor: float = DEFAULT_CONVERSION_FACTOR,
    ) -> dict[str, Any]:
        """Map the raw self-care payloads into flat coordinator data."""
        data: dict[str, Any] = {
            DATA_AVAILABLE: True,
            DATA_CONVERSION_FACTOR: conversion_factor,
        }
        cls._add_readings(data, raw, conversion_factor)
        cls._add_contract(data, raw)
        cls._add_invoices(data, raw, today)
        return data

    @staticmethod
    def _add_readings(
        data: dict[str, Any], raw: dict[str, Any], conversion_factor: float
    ) -> None:
        """Normalise the meter-reading history and the next reading window."""
        readings = _parse_readings(raw.get("readings"))
        if readings:
            data[DATA_READINGS] = readings
            latest = readings[-1]
            data[DATA_METER_INDEX] = latest["index"]
            # The supplier bills kWh, so mirror every volume in energy too.
            data[DATA_METER_INDEX_ENERGY] = round(
                latest["index"] * conversion_factor, 2
            )
            data[DATA_LAST_READING_ISO] = latest["iso"]
            data[DATA_LAST_READING_ORIGIN] = latest["origin"]

            if len(readings) >= 2:
                previous = readings[-2]
                volume = round(max(latest["index"] - previous["index"], 0.0), 3)
                data[DATA_LAST_CONSUMPTION] = volume
                data[DATA_LAST_CONSUMPTION_ENERGY] = round(
                    volume * conversion_factor, 2
                )
                data[DATA_LAST_CONSUMPTION_FROM] = previous["iso"]
                data[DATA_LAST_CONSUMPTION_DAYS] = (
                    date.fromisoformat(latest["iso"])
                    - date.fromisoformat(previous["iso"])
                ).days

        # The next expected reading window only comes with ``getLastMeterRead``.
        recent = raw.get("last_reading")
        if isinstance(recent, dict):
            data[DATA_NEXT_READING_START] = _parse_compact_date(
                recent.get("idealPeriodBegin")
            )
            data[DATA_NEXT_READING_END] = _parse_compact_date(
                recent.get("idealPeriodEnd")
            )
            if recent.get("deliveryPoint"):
                data[DATA_DELIVERY_POINT] = recent["deliveryPoint"]
            if recent.get("divisionText"):
                data[DATA_DIVISION_TEXT] = recent["divisionText"]

    @staticmethod
    def _add_contract(data: dict[str, Any], raw: dict[str, Any]) -> None:
        """Normalise contract metadata and the distributor's name."""
        contract = raw.get("contract")
        if isinstance(contract, dict):
            data[DATA_CONTRACT_STATUS] = contract.get("agreementStatus")
            data[DATA_CONTRACT_NUMBER] = contract.get("contractNumber")
            data[DATA_CONTRACT_TIER] = contract.get("tier")
            data[DATA_DIRECT_DEBIT] = bool(contract.get("directDebit"))
            data.setdefault(DATA_DELIVERY_POINT, contract.get("CUI"))

        distributor = raw.get("distributor")
        if isinstance(distributor, dict) and distributor.get("title"):
            data[DATA_DISTRIBUTOR] = distributor["title"]

    @staticmethod
    def _add_invoices(data: dict[str, Any], raw: dict[str, Any], today: date) -> None:
        """Normalise the latest invoice plus the aggregates over all invoices."""
        last_invoice = raw.get("last_invoice")
        invoice = (
            last_invoice.get("lastInvoice") if isinstance(last_invoice, dict) else None
        )
        if isinstance(invoice, dict):
            data[DATA_LAST_INVOICE_TOTAL] = _to_float(invoice.get("totalValue"))
            data[DATA_LAST_INVOICE_DUE] = _parse_iso_date(invoice.get("dueDate"))
            data[DATA_LAST_INVOICE_EMITTED] = _parse_iso_date(
                invoice.get("emissionDate")
            )
            data[DATA_LAST_INVOICE_PERIOD_START] = _parse_iso_date(
                invoice.get("startBillingPeriod")
            )
            data[DATA_LAST_INVOICE_PERIOD_END] = _parse_iso_date(
                invoice.get("endBillingPeriod")
            )
            data[DATA_LAST_INVOICE_STATUS] = invoice.get("paymentStatus")
            data[DATA_LAST_INVOICE_NUMBER] = _first_str(
                invoice.get("documentFiscalNumber")
            )

        payload = raw.get("invoices")
        invoices = payload.get("invoices") if isinstance(payload, dict) else None
        if not isinstance(invoices, list):
            return

        pending = [
            inv
            for inv in invoices
            if isinstance(inv, dict)
            and inv.get("paymentStatus") == INVOICE_STATUS_PENDING
        ]
        data[DATA_AMOUNT_DUE] = round(
            sum(_to_float(inv.get("totalValue")) or 0.0 for inv in pending), 2
        )
        data[DATA_INVOICE_PENDING] = bool(pending)
        data[DATA_DIRECT_DEBIT_FAILED] = any(
            isinstance(inv, dict) and inv.get("failedDirectDebit") for inv in invoices
        )

        cutoff = today - timedelta(days=BILLING_YEAR_DAYS)
        recent_total = 0.0
        for inv in invoices:
            if not isinstance(inv, dict):
                continue
            emitted = _parse_iso_date(inv.get("emissionDate"))
            if emitted is None or emitted < cutoff:
                continue
            recent_total += _to_float(inv.get("totalValue")) or 0.0
        data[DATA_BILLED_12M] = round(recent_total, 2)

        data[DATA_INVOICE_SERIES] = _invoice_series(invoices)


def _invoice_series(invoices: list[Any]) -> list[dict[str, Any]]:
    """Build ``[{"iso", "total"}]`` from the invoice list, oldest first.

    Each invoice is keyed by the **end of the period it bills**, not its emission
    date: that is when the money was actually incurred, and it lines the cost up
    with the consumption that produced it. Invoices closing on the same day are
    merged, which is what makes the resulting series safe to accumulate.
    """
    by_iso: dict[str, float] = {}
    for inv in invoices:
        if not isinstance(inv, dict):
            continue
        closes = _parse_iso_date(inv.get("endBillingPeriod")) or _parse_iso_date(
            inv.get("emissionDate")
        )
        total = _to_float(inv.get("totalValue"))
        if closes is None or total is None:
            continue
        iso = closes.isoformat()
        by_iso[iso] = round(by_iso.get(iso, 0.0) + total, 2)

    return [{"iso": iso, "total": by_iso[iso]} for iso in sorted(by_iso)]


def _parse_readings(payload: Any) -> list[dict[str, Any]]:
    """Build ``[{"iso", "index", "origin"}]`` from a meter-reading payload.

    Readings arrive as ``{"date": "30-07-2026", "gv": "320", "originText": ...}``
    with ``gv`` the cumulative meter index in m³ — occasionally with stray
    whitespace, hence the strict-but-forgiving parsing. Duplicated dates (the
    distributor and the client both reporting one day) collapse to the highest
    index for that day, and the result is sorted oldest first.
    """
    if not isinstance(payload, dict):
        return []
    raw_readings = payload.get("readings")
    if not isinstance(raw_readings, list):
        return []

    by_iso: dict[str, dict[str, Any]] = {}
    for item in raw_readings:
        if not isinstance(item, dict):
            continue
        iso = _parse_reading_date(item.get("date"))
        index = _to_float(item.get("gv"))
        if iso is None or index is None:
            continue
        existing = by_iso.get(iso)
        if existing is None or index > existing["index"]:
            by_iso[iso] = {
                "iso": iso,
                "index": index,
                "origin": item.get("originText"),
            }

    return [by_iso[iso] for iso in sorted(by_iso)]


def _parse_reading_date(raw: Any) -> str | None:
    """Convert a ``dd-mm-yyyy`` reading date to ``YYYY-MM-DD``."""
    if not isinstance(raw, str):
        return None
    parts = raw.strip().split("-")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(p) for p in parts)
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_compact_date(raw: Any) -> date | None:
    """Convert a compact ``yyyymmdd`` date (the reading window) to a ``date``."""
    if not isinstance(raw, str) or len(raw.strip()) != 8:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def _parse_iso_date(raw: Any) -> date | None:
    """Convert an invoice timestamp (``2026-08-17T00:00:00+0000``) to a ``date``.

    Invoice dates carry a midnight-UTC time component that means nothing, so the
    date part is what entities expose.
    """
    if not isinstance(raw, str):
        return None
    parsed = dt_util.parse_datetime(raw.strip())
    if parsed is not None:
        return parsed.date()
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _to_positive_float(raw: Any, default: float) -> float:
    """Coerce a configured number, falling back when absent or nonsensical."""
    value = _to_float(raw)
    if value is None or value <= 0:
        return default
    return value


def _to_float(raw: Any) -> float | None:
    """Coerce an API value (often a string, sometimes padded) into a float."""
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip().replace(",", "."))
        except ValueError:
            return None
    return None


def _first_str(raw: Any) -> str | None:
    """Return the first entry of the single-element lists the API likes to use."""
    if isinstance(raw, list) and raw:
        return str(raw[0])
    if isinstance(raw, str):
        return raw
    return None
