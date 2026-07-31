"""Import CUR meter readings as long-term external statistics.

The portal exposes a **cumulative meter index** (``gv``, in m³) per reading —
not per-period totals — which is exactly what the Home Assistant Gas dashboard
wants. What it does *not* expose is a daily series: the distributor reads the
meter roughly monthly, and a client-submitted reading lands whenever the user
files one, so readings are sparse and irregular.

Readings are also **backdated** — a reading dated the 20th is only visible days
later. A live ``total_increasing`` sensor would therefore attribute a whole
month's gas to the poll hour. Instead the readings are imported as external
statistics timestamped at **local midnight**, so the dashboard places the
consumption on the days the gas was actually burnt.

A reading reports the meter index for one instant but the *consumption* it
implies belongs to the whole period since the previous reading, so each delta is
**spread evenly over the days it covers** — one point per calendar day between
two readings. Dating the whole delta at the reading day instead put a month of
gas on a single day, which made calendar months read wrong: a reading taken on
11 July carries 30 days of gas, two thirds of it June's, and the Gas dashboard
diffs the ``sum`` at month boundaries. An even daily split is an approximation
(gas use is not uniform), but a far smaller one than attributing June's heating
to July.

The whole window is therefore **rewritten** on every poll rather than appended
to: the running ``sum`` is anchored on the statistic already stored for the
oldest reading in the window and recomputed from there, which also means a
backdated reading arriving late repairs the days it covers instead of dumping
its gas on the day it showed up.

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

from datetime import date, timedelta
from itertools import pairwise
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
    anchor_sum: float,
) -> list[dict[str, Any]]:
    """Return one ``{"iso", "state", "sum"}`` point per day the readings cover.

    ``sum`` is cumulative consumption and ``state`` the meter index, both
    interpolated linearly across the days between two readings: the gas a reading
    reports was burnt over that whole period, so the Gas dashboard's period diff
    (``sum[end] - sum[start]``) lands on the right calendar month instead of
    crediting a whole month to one reading day.

    ``anchor_sum`` is the total already stored for the **oldest** reading in the
    window; that reading itself therefore contributes no consumption. On a fresh
    install it is ``0.0`` — how much gas passed through the meter before the first
    reading is not knowable from this API.
    """
    ordered = _one_reading_per_day(readings)
    if not ordered:
        return []

    first = ordered[0]
    points: list[dict[str, Any]] = [
        {"iso": first["iso"], "state": first["index"], "sum": round(anchor_sum, 3)}
    ]
    running = anchor_sum

    for previous, entry in pairwise(ordered):
        start = date.fromisoformat(previous["iso"])
        span = (date.fromisoformat(entry["iso"]) - start).days
        step_index = (entry["index"] - previous["index"]) / span
        # A meter that goes backwards means a replaced or rolled-over meter; write
        # the drop off rather than break the statistic's monotonic ``sum``. The
        # ``state`` still follows the real index, so the series ends where the
        # meter is.
        step_sum = max(entry["index"] - previous["index"], 0.0) / span

        for day in range(1, span + 1):
            points.append(
                {
                    "iso": (start + timedelta(days=day)).isoformat(),
                    "state": round(previous["index"] + step_index * day, 3),
                    "sum": round(running + step_sum * day, 3),
                }
            )
        running += step_sum * span

    return points


def _one_reading_per_day(readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort readings oldest first, keeping the highest index per day.

    The coordinator already collapses duplicate days, but the interpolation below
    divides by the gap between consecutive readings, so a repeated day would be a
    division by zero. Defend here rather than trust the caller.
    """
    highest: dict[str, dict[str, Any]] = {}
    for entry in readings:
        current = highest.get(entry["iso"])
        if current is None or entry["index"] > current["index"]:
            highest[entry["iso"]] = entry
    return [highest[iso] for iso in sorted(highest)]


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
    """Import the meter readings as this contract's volume and energy statistics.

    The three series are handled differently on purpose:

    - **volume (m³)** is the source of truth. The window the portal returns is
      rewritten wholesale (see ``build_statistic_points``) so consumption sits on
      the days it was burnt; ``sum`` is anchored on what is already stored for the
      oldest reading in that window, which keeps the series monotonic and leaves
      history older than the window alone;
    - **energy (kWh)** is purely derived (``volume x conversion_factor``) and is
      therefore re-synced from the volume series on every poll. Correcting the
      factor then fixes the *whole* history instead of leaving the old factor baked
      into everything written before the change;
    - **cost** is appended from ``invoices`` (see ``build_cost_points``) so the Gas
      dashboard can show money without a price entity. It stays dated at the close
      of the period it bills: an invoice is a single event, not a daily accrual.
    """
    # The recorder is a declared dependency but must be imported lazily: the
    # module pulls in native deps that are intentionally absent from the unit
    # test environment (see tests/test_statistics.py, which covers the pure
    # ``build_statistic_points`` decision logic instead).
    from homeassistant.components.recorder.models import StatisticData
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
    )

    if not readings:
        return

    volume_id = statistic_id_for(contract_number)
    energy_id = energy_statistic_id_for(contract_number)

    # --- 1. Volume: the source of truth, rewritten over the polled window ---
    stored = await _async_read_series(hass, volume_id)
    oldest_iso = min(entry["iso"] for entry in readings)
    points = build_statistic_points(readings, _anchor_sum(stored, oldest_iso))
    fresh: list[StatisticData] = [
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
        hass, energy_id, contract_number, conversion_factor, stored, fresh
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


async def _async_read_series(
    hass: HomeAssistant, statistic_id: str
) -> dict[float, tuple[float, float]]:
    """Return the whole stored series as ``{epoch_seconds: (state, sum)}``.

    Read once per poll and used twice: to anchor the volume series' running total
    on what is already stored, and as the base the energy series is derived from.
    """
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {statistic_id},
        "day",
        None,
        {"state", "sum"},
    )

    series: dict[float, tuple[float, float]] = {}
    for row in rows.get(statistic_id) or []:
        state, total = row.get("state"), row.get("sum")
        if state is not None and total is not None:
            series[_as_seconds(row["start"])] = (float(state), float(total))
    return series


def _anchor_sum(stored: dict[float, tuple[float, float]], oldest_iso: str) -> float:
    """Return the total already stored at ``oldest_iso``, else the last one before it.

    The oldest reading the portal still returns contributes no consumption of its
    own — either it is the very first reading ever (nothing before it is knowable)
    or its delta was counted when it was imported, before it aged out of the
    window. Either way the rewrite has to resume from the total stored *at* that
    day, and never from zero, or a slid window would reset the series and the Gas
    dashboard would render the reset as a spike.
    """
    earlier = [ts for ts in stored if _ts_to_local_iso(ts) <= oldest_iso]
    if not earlier:
        return 0.0
    return stored[max(earlier)][1]


async def _async_sync_energy_series(
    hass: HomeAssistant,
    energy_id: str,
    contract_number: str,
    conversion_factor: float,
    stored_volume: dict[float, tuple[float, float]],
    fresh_volume: list[Any],
) -> None:
    """Mirror the volume statistic into the energy one at the current factor.

    Rewrites points in place (``async_add_external_statistics`` overwrites a point
    with the same ``start``), which keeps ``energy == volume x factor`` true for
    every point rather than only for newly appended ones.

    ``fresh_volume`` holds the points written moments ago in this same call, and
    has to be merged over ``stored_volume`` explicitly:
    ``async_add_external_statistics`` *queues* the write on the recorder thread, so
    the series read at the start of this poll cannot contain them — which would
    leave the energy series a poll behind, and empty on a fresh install.
    """
    from homeassistant.components.recorder.models import StatisticData
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
    )

    # Keyed by the point's instant so the just-written points win over any stored
    # row for the same day.
    merged = dict(stored_volume)
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
