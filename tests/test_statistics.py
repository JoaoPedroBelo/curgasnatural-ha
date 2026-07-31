"""Tests for the statistics importer's decision logic.

``build_statistic_points`` is the part worth pinning down: it spreads the
consumption a reading reports over the days that reading actually covers, and
turns an absolute meter index into the monotonic ``sum`` the Gas dashboard diffs.
"""

from datetime import date, timedelta

from homeassistant.util import dt as dt_util
import pytest

from custom_components.curgasnatural.statistics import (
    _anchor_sum,
    _ts_to_local_iso,
    build_cost_points,
    build_statistic_points,
    cost_statistic_id_for,
    energy_statistic_id_for,
    statistic_id_for,
)

READINGS = [
    {"iso": "2026-05-15", "index": 250.0},
    {"iso": "2026-06-11", "index": 270.0},
    {"iso": "2026-07-11", "index": 300.0},
    {"iso": "2026-07-30", "index": 320.0},
]


def sums_by_iso(points: list[dict]) -> dict[str, float]:
    """Index a point list by day for readable assertions."""
    return {p["iso"]: p["sum"] for p in points}


def test_every_day_between_the_first_and_last_reading_gets_a_point():
    points = build_statistic_points(READINGS, 0.0)

    span = (date(2026, 7, 30) - date(2026, 5, 15)).days + 1
    assert len(points) == span
    assert points[0]["iso"] == "2026-05-15"
    assert points[-1]["iso"] == "2026-07-30"
    # Contiguous, one per calendar day, no gaps and no duplicates.
    days = [date.fromisoformat(p["iso"]) for p in points]
    assert days == [days[0] + timedelta(days=i) for i in range(span)]


def test_the_oldest_reading_contributes_no_consumption():
    """How much gas passed before the first reading is not knowable."""
    points = build_statistic_points(READINGS, 0.0)

    assert points[0]["sum"] == 0.0


def test_each_reading_day_lands_on_the_exact_meter_delta():
    """Spreading must not lose or invent gas: reading days stay exact."""
    at = sums_by_iso(build_statistic_points(READINGS, 0.0))

    assert at["2026-06-11"] == pytest.approx(20.0)  # 270 - 250
    assert at["2026-07-11"] == pytest.approx(50.0)  # + (300 - 270)
    assert at["2026-07-30"] == pytest.approx(70.0)  # + (320 - 300)


def test_a_reading_delta_is_spread_evenly_over_the_days_it_covers():
    at = sums_by_iso(build_statistic_points(READINGS, 0.0))

    # 250 -> 270 over 27 days: every day in between carries 20/27 m³.
    per_day = 20.0 / 27
    assert at["2026-05-16"] == pytest.approx(per_day, abs=1e-3)
    assert at["2026-05-20"] == pytest.approx(5 * per_day, abs=1e-3)


def test_a_month_no_longer_absorbs_the_previous_month_s_gas():
    """Regression test for the bug this spreading exists to fix.

    Dating each reading's whole delta at the reading day made July report 50 m³
    when 19 of those were read on 11 July after 30 days — i.e. mostly June's gas.
    """
    at = sums_by_iso(build_statistic_points(READINGS, 0.0))

    july = at["2026-07-30"] - at["2026-06-30"]

    # 270 -> 300 spans 11 June -> 11 July, so only 11/30 of it belongs to July.
    assert july == pytest.approx(30.0 * 11 / 30 + 20.0, abs=1e-3)
    assert july < 50.0


def test_state_interpolates_the_meter_index_between_readings():
    points = build_statistic_points(READINGS, 0.0)
    at = {p["iso"]: p["state"] for p in points}

    # Reading days carry the index the portal actually reported.
    assert at["2026-05-15"] == 250.0
    assert at["2026-06-11"] == pytest.approx(270.0)
    assert at["2026-07-30"] == pytest.approx(320.0)
    # Days in between ramp linearly, never backwards.
    assert at["2026-05-16"] == pytest.approx(250.0 + 20.0 / 27, abs=1e-3)
    states = [p["state"] for p in points]
    assert states == sorted(states)


def test_the_running_sum_continues_from_the_stored_anchor():
    """The oldest reading in the window keeps the sum already stored for it."""
    at = sums_by_iso(build_statistic_points(READINGS, 100.0))

    assert at["2026-05-15"] == 100.0
    assert at["2026-07-30"] == pytest.approx(170.0)


def test_a_single_reading_yields_a_single_point():
    points = build_statistic_points([READINGS[0]], 5.0)

    assert points == [{"iso": "2026-05-15", "state": 250.0, "sum": 5.0}]


def test_empty_series_yields_no_points():
    assert build_statistic_points([], 0.0) == []


def test_sum_never_decreases_when_the_meter_goes_backwards():
    replaced_meter = [
        {"iso": "2026-07-01", "index": 320.0},
        # A replaced meter restarts near zero; consumption must not go negative.
        {"iso": "2026-07-15", "index": 2.0},
        {"iso": "2026-07-30", "index": 9.0},
    ]

    points = build_statistic_points(replaced_meter, 0.0)
    at = sums_by_iso(points)
    sums = [p["sum"] for p in points]

    assert sums == sorted(sums)
    # The drop itself is written off; only the new meter's own 7 m³ counts.
    assert at["2026-07-15"] == 0.0
    assert at["2026-07-30"] == pytest.approx(7.0)
    # The state still tracks the real index, so the series ends where the meter is.
    assert points[-1]["state"] == pytest.approx(9.0)


def test_unsorted_input_is_ordered_before_accumulating():
    shuffled = [READINGS[2], READINGS[0], READINGS[3], READINGS[1]]

    assert build_statistic_points(shuffled, 0.0) == build_statistic_points(
        READINGS, 0.0
    )


def test_two_readings_for_one_day_collapse_to_the_highest_index():
    """The distributor and the client can both report the same day."""
    same_day = [
        {"iso": "2026-07-11", "index": 300.0},
        {"iso": "2026-07-30", "index": 310.0},
        {"iso": "2026-07-30", "index": 320.0},
    ]

    points = build_statistic_points(same_day, 0.0)

    assert len(points) == (date(2026, 7, 30) - date(2026, 7, 11)).days + 1
    assert points[-1]["state"] == pytest.approx(320.0)
    assert points[-1]["sum"] == pytest.approx(20.0)


def stored_at(*days: tuple[str, float]) -> dict[float, tuple[float, float]]:
    """Build a stored series keyed the way the recorder reports it."""
    return {
        dt_util.start_of_local_day(date.fromisoformat(iso)).timestamp(): (0.0, total)
        for iso, total in days
    }


def test_a_fresh_install_anchors_the_running_total_at_zero():
    assert _anchor_sum({}, "2026-05-15") == 0.0


def test_the_anchor_is_the_total_already_stored_for_the_oldest_reading():
    stored = stored_at(("2026-05-15", 113.0), ("2026-06-11", 123.0))

    assert _anchor_sum(stored, "2026-05-15") == 113.0


def test_a_slid_window_anchors_on_the_last_total_before_it():
    """The portal only returns ~400 days, so older points must not be re-counted."""
    stored = stored_at(("2024-01-10", 40.0), ("2025-02-20", 90.0))

    # Nothing stored for the oldest reading still returned; resume from before it.
    assert _anchor_sum(stored, "2026-05-15") == 90.0


def test_totals_stored_after_the_oldest_reading_are_ignored():
    """They are about to be rewritten, so they cannot seed the running total."""
    stored = stored_at(("2026-06-11", 123.0), ("2026-07-30", 137.0))

    assert _anchor_sum(stored, "2026-05-15") == 0.0


def test_statistic_id_is_namespaced_per_contract():
    assert (
        statistic_id_for("34_00000000_00000000")
        == "curgasnatural:consumption_34_00000000_00000000"
    )
    # Different contracts must not share a statistic.
    assert statistic_id_for("31_1_2") != statistic_id_for("34_1_2")


def test_recorder_timestamps_are_read_as_local_dates():
    """``get_last_statistics`` returns epoch seconds, but has used ms too."""
    seconds = 1785456000  # 2026-07-31T12:00:00Z
    assert _ts_to_local_iso(seconds) == _ts_to_local_iso(seconds * 1000)
    assert _ts_to_local_iso(seconds).startswith("2026-07-3")


INVOICES = [
    {"iso": "2026-05-16", "total": 30.11},
    {"iso": "2026-06-20", "total": 25.67},
    {"iso": "2026-07-20", "total": 12.34},
]


def test_cost_points_accumulate_invoice_totals():
    points = build_cost_points(INVOICES, None, 0.0)

    assert [p["iso"] for p in points] == [i["iso"] for i in INVOICES]
    assert [p["state"] for p in points] == [30.11, 25.67, 12.34]
    assert [p["sum"] for p in points] == [30.11, 55.78, 68.12]


def test_cost_points_continue_from_what_is_stored():
    points = build_cost_points(INVOICES, "2026-06-20", 55.78)

    assert [p["iso"] for p in points] == ["2026-07-20"]
    assert points[0]["sum"] == 68.12


def test_cost_points_are_empty_when_nothing_is_new():
    assert build_cost_points(INVOICES, "2026-07-20", 68.12) == []
    assert build_cost_points([], None, 0.0) == []


def test_a_credit_note_never_makes_the_cost_sum_fall():
    """A falling sum reads as a reset to HA and renders as a huge negative bar."""
    with_credit = [
        {"iso": "2026-05-16", "total": 30.11},
        {"iso": "2026-06-20", "total": -5.00},
        {"iso": "2026-07-20", "total": 12.34},
    ]

    sums = [p["sum"] for p in build_cost_points(with_credit, None, 0.0)]

    assert sums == sorted(sums)
    assert sums == [30.11, 30.11, 42.45]


def test_cost_points_sort_unordered_input():
    shuffled = [INVOICES[2], INVOICES[0], INVOICES[1]]

    assert build_cost_points(shuffled, None, 0.0) == build_cost_points(
        INVOICES, None, 0.0
    )


def test_the_three_statistic_ids_are_distinct():
    contract = "34_00000000_00000000"
    ids = {
        statistic_id_for(contract),
        energy_statistic_id_for(contract),
        cost_statistic_id_for(contract),
    }

    assert len(ids) == 3
    assert cost_statistic_id_for(contract) == (
        "curgasnatural:cost_34_00000000_00000000"
    )
