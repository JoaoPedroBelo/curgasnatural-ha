"""Import CUR meter readings as long-term external statistics.

The portal exposes a **cumulative meter index** (``gv``, in m³) per reading —
not per-period totals — which is exactly what the Home Assistant Gas dashboard
wants. What it does *not* expose is a daily series: the distributor reads the
meter roughly monthly, and a client-submitted reading lands whenever the user
files one, so readings are sparse and irregular.

Readings are also **backdated** — a reading dated the 20th is only visible days
later. A live ``total_increasing`` sensor would therefore attribute a whole
month's gas to the poll hour. Instead each reading is imported as an hourly
external statistic timestamped at that reading's **local midnight**, so the
dashboard places the consumption on the day the meter was actually read.

Consequence worth knowing: consumption appears as one bar per reading, covering
the whole period since the previous one. That is genuinely all the data there
is — we do not spread it over the intervening days, because that would be
invented detail.

Because we re-poll a sliding window of readings, the running ``sum`` is
continued from the last statistic already stored (queried via
``get_last_statistics``) and only readings newer than that are appended —
keeping ``sum`` monotonic and never re-writing history.

Three series are published per contract:

===================================  =========  ==============================
Series                               Unit       How it is maintained
===================================  =========  ==============================
``consumption_<contract>``           m³         append-only, source of truth
``energy_<contract>``                kWh        derived from volume x factor
``cost_<contract>``                  currency   append-only, from invoices
===================================  =========  ==============================

The energy series is *derived* rather than accumulated alongside the volume one,
so a corrected conversion factor repairs the whole history rather than only its
tail. The cost series carries what the supplier actually invoiced — Home Assistant
will not accept a price entity on an external statistic, and billed totals already
include fixed terms and VAT, which price x consumption never would.
"""

from __future__ import annotations

from datetime import date
import logging
from typing import Any, Final

from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STATISTIC_NAME_PREFIX: Final = "CUR Gás Natural Consumption"
ENERGY_STATISTIC_NAME_PREFIX: Final = "CUR Gás Natural Energy"
COST_STATISTIC_NAME_PREFIX: Final = "CUR Gás Natural Cost"


def statistic_id_for(contract_number: str) -> str:
    """Return the external statistic id for one contract.

    External ids use ``<domain>:<object_id>`` (a colon, not a dot), and the
    object id must be a slug — hence ``slugify`` over the contract number, which
    keeps one statistic per delivery point when several contracts are set up.
    """
    return f"{DOMAIN}:consumption_{slugify(contract_number)}"


def cost_statistic_id_for(contract_number: str) -> str:
    """Return the external statistic id carrying billed cost in the local currency.

    Home Assistant refuses ``entity_energy_price``/``number_energy_price`` on an
    external statistic ("Use stat_cost instead"), so a Gas dashboard fed by this
    integration needs a cost *statistic* to show money. This one carries what the
    supplier actually invoiced rather than price x consumption, which means it
    already includes fixed terms, taxes and VAT.
    """
    return f"{DOMAIN}:cost_{slugify(contract_number)}"


def energy_statistic_id_for(contract_number: str) -> str:
    """Return the external statistic id carrying the same series in kWh.

    Both units are published: m³ is what the meter counts, kWh is what the
    supplier bills and what a €/kWh tariff must be multiplied by, so a Gas
    dashboard configured with a price entity needs this one.
    """
    return f"{DOMAIN}:energy_{slugify(contract_number)}"


def build_statistic_points(
    readings: list[dict[str, Any]],
    last_iso: str | None,
    last_sum: float,
) -> list[dict[str, Any]]:
    """Return ``[{"iso", "state", "sum"}]`` for readings newer than ``last_iso``.

    ``state`` is the meter index itself; ``sum`` is cumulative consumption,
    advanced by the delta between consecutive readings so the Gas dashboard's
    period diff (``sum[n] - sum[n-1]``) yields exactly the m³ read in between.

    The oldest reading of a fresh install has no predecessor, so it contributes
    no consumption (its ``sum`` equals the starting total): how much gas passed
    through the meter *before* that reading is not knowable from this API.

    Readings already imported (``iso <= last_iso``) are skipped but still used
    as the delta baseline for the first new one.
    """
    running = last_sum
    prev_index: float | None = None
    points: list[dict[str, Any]] = []

    for entry in sorted(readings, key=lambda e: e["iso"]):
        index = entry["index"]
        if last_iso is not None and entry["iso"] <= last_iso:
            prev_index = index
            continue
        if prev_index is not None:
            # A meter that goes backwards means a replaced or rolled-over meter;
            # clamp at zero rather than break the statistic's monotonic ``sum``.
            running = round(running + max(index - prev_index, 0.0), 3)
        points.append({"iso": entry["iso"], "state": index, "sum": running})
        prev_index = index

    return points


def build_cost_points(
    invoices: list[dict[str, Any]],
    last_iso: str | None,
    last_sum: float,
) -> list[dict[str, Any]]:
    """Return ``[{"iso", "state", "sum"}]`` for invoices newer than ``last_iso``.

    ``sum`` accumulates invoice totals so the dashboard's period diff yields what
    was billed for that period. A non-positive total (a credit note, or a
    correction) is **clamped to zero**: letting the running sum fall would make
    Home Assistant read it as a meter reset and render a large negative bar, which
    is far more misleading than under-reporting one credit.
    """
    running = last_sum
    points: list[dict[str, Any]] = []

    for entry in sorted(invoices, key=lambda e: e["iso"]):
        if last_iso is not None and entry["iso"] <= last_iso:
            continue
        total = max(float(entry["total"]), 0.0)
        running = round(running + total, 2)
        points.append({"iso": entry["iso"], "state": total, "sum": running})

    return points


async def async_import_consumption_statistics(
    hass: HomeAssistant,
    contract_number: str,
    readings: list[dict[str, Any]],
    conversion_factor: float,
    invoices: list[dict[str, Any]] | None = None,
) -> None:
    """Import new meter readings as this contract's volume and energy statistics.

    The two series are handled differently on purpose:

    - **volume (m³)** is the source of truth and append-only, so history is never
      rewritten and ``sum`` stays monotonic;
    - **energy (kWh)** is purely derived (``volume x conversion_factor``) and is
      therefore re-synced from the volume series on every poll. Correcting the
      factor then fixes the *whole* history instead of leaving the old factor baked
      into everything written before the change;
    - **cost** is appended from ``invoices`` (see ``build_cost_points``) so the Gas
      dashboard can show money without a price entity.
    """
    # The recorder is a declared dependency but must be imported lazily: the
    # module pulls in native deps that are intentionally absent from the unit
    # test environment (see tests/test_statistics.py, which covers the pure
    # ``build_statistic_points`` decision logic instead).
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
        get_last_statistics,
    )

    if not readings:
        return

    volume_id = statistic_id_for(contract_number)
    energy_id = energy_statistic_id_for(contract_number)

    # --- 1. Volume: the source of truth, append-only ------------------------
    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, volume_id, True, {"sum"}
    )
    stored = (last_stats.get(volume_id) or [None])[0]
    if stored:
        last_sum = float(stored.get("sum") or 0.0)
        last_iso: str | None = _ts_to_local_iso(float(stored["start"]))
    else:
        last_sum = 0.0
        last_iso = None

    points = build_statistic_points(readings, last_iso, last_sum)
    fresh = [
        {
            "start": dt_util.start_of_local_day(date.fromisoformat(p["iso"])),
            "state": p["state"],
            "sum": p["sum"],
        }
        for p in points
    ]
    if fresh:
        async_add_external_statistics(
            hass,
            _metadata(
                volume_id,
                f"{STATISTIC_NAME_PREFIX} ({contract_number})",
                UnitOfVolume.CUBIC_METERS,
            ),
            fresh,
        )

    # --- 2. Energy: derived, so recomputed from the volume series ----------
    # Deriving rather than accumulating in parallel is what makes a corrected
    # conversion factor fix the *whole* history. Accumulating independently would
    # leave the old factor baked into every point already written, i.e. a step in
    # the middle of the series that no amount of re-polling repairs.
    await _async_sync_energy_series(
        hass, volume_id, energy_id, contract_number, conversion_factor, fresh
    )

    # --- 3. Cost: what the supplier actually invoiced -----------------------
    if invoices:
        await _async_import_cost_series(hass, contract_number, invoices)

    _LOGGER.debug(
        "Imported %d volume point(s) for %s; energy series synced at %.5f kWh/m³",
        len(points),
        contract_number,
        conversion_factor,
    )


async def _async_import_cost_series(
    hass: HomeAssistant,
    contract_number: str,
    invoices: list[dict[str, Any]],
) -> None:
    """Append newly issued invoices to this contract's cost statistic.

    Append-only, like the volume series: an invoice, once issued, is history. The
    unit must be the instance's own currency or Home Assistant rejects the
    statistic as a cost source.
    """
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.models import StatisticData
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
        get_last_statistics,
    )

    currency = hass.config.currency
    if not currency:
        _LOGGER.debug("No currency configured; skipping the cost statistic")
        return

    statistic_id = cost_statistic_id_for(contract_number)
    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    stored = (last_stats.get(statistic_id) or [None])[0]
    if stored:
        last_sum = float(stored.get("sum") or 0.0)
        last_iso: str | None = _ts_to_local_iso(float(stored["start"]))
    else:
        last_sum = 0.0
        last_iso = None

    points = build_cost_points(invoices, last_iso, last_sum)
    if not points:
        return

    statistics: list[StatisticData] = [
        {
            "start": dt_util.start_of_local_day(date.fromisoformat(p["iso"])),
            "state": p["state"],
            "sum": p["sum"],
        }
        for p in points
    ]
    async_add_external_statistics(
        hass,
        _metadata(
            statistic_id,
            f"{COST_STATISTIC_NAME_PREFIX} ({contract_number})",
            currency,
        ),
        statistics,
    )
    _LOGGER.debug(
        "Imported %d invoice cost point(s) up to %s for %s",
        len(points),
        points[-1]["iso"],
        contract_number,
    )


def _metadata(statistic_id: str, name: str, unit: str) -> Any:
    """Build the metadata every series here shares."""
    return {
        "has_mean": False,
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": statistic_id,
        "unit_of_measurement": unit,
    }


async def _async_sync_energy_series(
    hass: HomeAssistant,
    volume_id: str,
    energy_id: str,
    contract_number: str,
    conversion_factor: float,
    fresh_volume: list[Any],
) -> None:
    """Mirror the volume statistic into the energy one at the current factor.

    Rewrites points in place (``async_add_external_statistics`` overwrites a point
    with the same ``start``), which keeps ``energy == volume x factor`` true for
    every point rather than only for newly appended ones.

    ``fresh_volume`` holds the points written moments ago in this same call. They
    have to be merged in explicitly: ``async_add_external_statistics`` *queues* the
    write on the recorder thread, so reading the volume series straight back would
    miss them and leave the energy series a poll behind — empty on a fresh install.
    """
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.models import StatisticData
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
        statistics_during_period,
    )

    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {volume_id},
        "day",
        None,
        {"state", "sum"},
    )

    # Keyed by the point's instant so the just-written points win over any stored
    # row for the same day.
    merged: dict[float, tuple[float, float]] = {}
    for row in rows.get(volume_id) or []:
        state, total = row.get("state"), row.get("sum")
        if state is not None and total is not None:
            merged[_as_seconds(row["start"])] = (float(state), float(total))
    for point in fresh_volume:
        merged[point["start"].timestamp()] = (
            float(point["state"]),
            float(point["sum"]),
        )
    if not merged:
        return

    statistics: list[StatisticData] = [
        {
            "start": dt_util.utc_from_timestamp(when),
            "state": round(state * conversion_factor, 3),
            "sum": round(total * conversion_factor, 3),
        }
        for when, (state, total) in sorted(merged.items())
    ]
    async_add_external_statistics(
        hass,
        _metadata(
            energy_id,
            f"{ENERGY_STATISTIC_NAME_PREFIX} ({contract_number})",
            UnitOfEnergy.KILO_WATT_HOUR,
        ),
        statistics,
    )


def _as_seconds(start: Any) -> float:
    """Normalise a recorder ``start`` to epoch seconds."""
    value = float(start)
    return value / 1000.0 if value > 1e11 else value


def _ts_to_local_iso(start_ts: float) -> str:
    """Convert a recorder ``start`` timestamp to a local ``YYYY-MM-DD`` date.

    ``get_last_statistics`` returns ``start`` as epoch seconds, but some HA
    versions have used milliseconds; normalise defensively (a 2020+ date is
    ~1.6e9 s vs ~1.6e12 ms, so the split is unambiguous).
    """
    if start_ts > 1e11:
        start_ts /= 1000.0
    local = dt_util.utc_from_timestamp(start_ts).astimezone(dt_util.DEFAULT_TIME_ZONE)
    return local.date().isoformat()
