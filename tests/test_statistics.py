"""Tests for the statistics importer's decision logic.

``build_statistic_points`` is the part worth pinning down: it decides which
readings are new, and turns an absolute meter index into the monotonic ``sum``
the Gas dashboard diffs.
"""

from custom_components.curgasnatural.statistics import (
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


def test_fresh_install_imports_every_reading_from_zero():
    points = build_statistic_points(READINGS, None, 0.0)

    assert [p["iso"] for p in points] == [r["iso"] for r in READINGS]
    # The oldest reading has no predecessor, so it contributes no consumption.
    assert points[0]["sum"] == 0.0
    assert points[1]["sum"] == 10.0  # 138 - 128
    assert points[2]["sum"] == 15.0  # + (143 - 138)
    assert points[3]["sum"] == 18.0  # + (146 - 143)


def test_state_is_always_the_meter_index():
    points = build_statistic_points(READINGS, None, 0.0)

    assert [p["state"] for p in points] == [250.0, 270.0, 300.0, 320.0]


def test_already_imported_readings_are_skipped_but_seed_the_delta():
    points = build_statistic_points(READINGS, "2026-07-11", 15.0)

    assert [p["iso"] for p in points] == ["2026-07-30"]
    # Continues from the stored sum with the 143 -> 146 delta.
    assert points[0]["sum"] == 18.0


def test_nothing_new_yields_no_points():
    assert build_statistic_points(READINGS, "2026-07-30", 18.0) == []


def test_empty_series_yields_no_points():
    assert build_statistic_points([], None, 0.0) == []


def test_sum_never_decreases_when_the_meter_goes_backwards():
    replaced_meter = [
        {"iso": "2026-07-01", "index": 320.0},
        # A replaced meter restarts near zero; consumption must not go negative.
        {"iso": "2026-07-15", "index": 2.0},
        {"iso": "2026-07-30", "index": 9.0},
    ]

    points = build_statistic_points(replaced_meter, None, 0.0)
    sums = [p["sum"] for p in points]

    assert sums == sorted(sums)
    assert sums == [0.0, 0.0, 7.0]


def test_unsorted_input_is_ordered_before_accumulating():
    shuffled = [READINGS[2], READINGS[0], READINGS[3], READINGS[1]]

    assert build_statistic_points(shuffled, None, 0.0) == build_statistic_points(
        READINGS, None, 0.0
    )


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
