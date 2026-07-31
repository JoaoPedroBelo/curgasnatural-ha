"""End-to-end setup of the integration inside a real Home Assistant instance.

Everything else in this suite tests our own logic in isolation. This module boots
Home Assistant, adds a config entry, and asserts the entities and the long-term
statistic actually materialise — the class of failure (illegal device_class,
recorder wiring, entity naming) that only shows up once HA is in the loop.

The portal is mocked at the client boundary; nothing here touches the network.

Every test that reads statistics back must call ``async_wait_recording_done`` first:
``async_add_external_statistics`` *queues* the write on the recorder thread, and
``hass.async_block_till_done()`` does not flush that queue. Skipping it makes the
assertions race the recorder — they pass on a fast machine and fail in CI.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.curgasnatural.const import (
    CONF_ADDRESS,
    CONF_CONTRACT_ID,
    CONF_CONTRACT_NUMBER,
    CONF_CONVERSION_FACTOR,
    CONF_CUI,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
    POLL_HOURS,
)
from custom_components.curgasnatural.statistics import (
    cost_statistic_id_for,
    energy_statistic_id_for,
    statistic_id_for,
)

from .conftest import (
    TEST_ADDRESS,
    TEST_CONTRACT_ID,
    TEST_CONTRACT_NUMBER,
    TEST_CUI,
    TEST_EMAIL,
)

ENTRY_DATA = {
    CONF_EMAIL: TEST_EMAIL,
    CONF_PASSWORD: "secret",
    CONF_CONTRACT_ID: TEST_CONTRACT_ID,
    CONF_CONTRACT_NUMBER: TEST_CONTRACT_NUMBER,
    CONF_CUI: TEST_CUI,
    CONF_ADDRESS: TEST_ADDRESS,
    CONF_CONVERSION_FACTOR: 11.20808,
}


@pytest.fixture(autouse=True)
def _ha_environment(recorder_mock, enable_custom_integrations):
    """Prepare the HA environment for every test in this module.

    Order matters: ``recorder_mock`` must be built before anything pulls in the
    ``hass`` fixture (``recorder_db_url`` asserts hass has not started yet), and
    ``enable_custom_integrations`` does pull it in. Requesting them in this order
    inside one autouse fixture is what pins that sequence.
    """


@pytest.fixture
def mock_client(raw_payload):
    """Patch the client the coordinator instantiates."""
    client = AsyncMock()
    client.async_get_data = AsyncMock(return_value=raw_payload)
    client.close = AsyncMock()
    with patch(
        "custom_components.curgasnatural.coordinator.CurGasNaturalClient",
        return_value=client,
    ):
        yield client


async def setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add and set up a config entry, returning it once loaded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id=TEST_CONTRACT_ID,
        title=TEST_ADDRESS,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entry_loads(hass, mock_client):
    entry = await setup_entry(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert DOMAIN in hass.data


async def test_all_entities_are_created(hass, mock_client):
    entry = await setup_entry(hass)
    registry = er.async_get(hass)

    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    by_platform: dict[str, int] = {}
    for e in entities:
        by_platform[e.domain] = by_platform.get(e.domain, 0) + 1

    assert by_platform == {"sensor": 12, "binary_sensor": 3}


async def test_entity_states_carry_the_normalised_values(hass, mock_client):
    await setup_entry(hass)

    index = hass.states.get("sensor.r_example_1_2_3_meter_index")
    assert index is not None, sorted(hass.states.async_entity_ids("sensor"))
    assert index.state == "320.0"
    assert index.attributes["unit_of_measurement"] == "m³"
    assert index.attributes["device_class"] == "gas"
    assert index.attributes["state_class"] == "total"
    assert index.attributes["reading_date"] == "2026-07-30"


async def test_period_consumption_state(hass, mock_client):
    await setup_entry(hass)

    delta = hass.states.get("sensor.r_example_1_2_3_consumption_since_previous_reading")
    assert delta is not None
    assert delta.state == "20.0"
    assert delta.attributes["days"] == 19
    # No device_class: HA forbids gas + measurement.
    assert "device_class" not in delta.attributes


async def test_date_and_money_states(hass, mock_client):
    await setup_entry(hass)

    due = hass.states.get("sensor.r_example_1_2_3_last_invoice_due_date")
    assert due is not None
    assert due.state == "2026-08-17"

    amount = hass.states.get("sensor.r_example_1_2_3_amount_due")
    assert amount is not None
    assert amount.state == "12.34"
    assert amount.attributes["device_class"] == "monetary"


async def test_binary_sensor_states(hass, mock_client):
    await setup_entry(hass)

    pending = hass.states.get("binary_sensor.r_example_1_2_3_invoice_pending_payment")
    assert pending is not None
    assert pending.state == "on"

    failed = hass.states.get("binary_sensor.r_example_1_2_3_direct_debit_failed")
    assert failed is not None
    assert failed.state == "off"


async def test_availability_sensor_is_not_registered_by_default(hass, mock_client):
    entry = await setup_entry(hass)
    registry = er.async_get(hass)

    available = next(
        e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id.endswith("_available")
    )

    assert available.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_one_device_named_after_the_supply_address(hass, mock_client):
    entry = await setup_entry(hass)
    registry = dr.async_get(hass)

    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)

    assert len(devices) == 1
    assert devices[0].name == TEST_ADDRESS
    assert devices[0].serial_number == TEST_CUI
    assert devices[0].manufacturer == "Lisboagás Comercialização, S.A."


async def test_consumption_statistic_is_imported(hass, mock_client):
    """The Gas dashboard source must exist after the first poll."""
    from homeassistant.components.recorder.statistics import statistics_during_period

    await setup_entry(hass)
    await async_wait_recording_done(hass)

    statistic_id = statistic_id_for(TEST_CONTRACT_NUMBER)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {statistic_id},
        "day",
        None,
        {"state", "sum"},
    )

    series = stats.get(statistic_id)
    assert series, f"no statistics under {statistic_id}: {list(stats)}"
    # 4 distinct reading days in the fixture (two entries share 30-07).
    assert len(series) == 4
    # sum accumulates the deltas: 0, +100, +100, +20 -> 220 over the window.
    assert series[-1]["sum"] == pytest.approx(220.0)
    assert series[-1]["state"] == pytest.approx(320.0)


async def test_a_second_poll_does_not_duplicate_statistics(hass, mock_client):
    """Re-polling the same sliding window must not double-count consumption."""
    from homeassistant.components.recorder.statistics import statistics_during_period

    await setup_entry(hass)
    await async_wait_recording_done(hass)

    coordinator = next(iter(hass.data[DOMAIN].values()))
    await coordinator.async_refresh()
    await async_wait_recording_done(hass)

    statistic_id = statistic_id_for(TEST_CONTRACT_NUMBER)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {statistic_id},
        "day",
        None,
        {"state", "sum"},
    )

    series = stats[statistic_id]
    assert len(series) == 4
    assert series[-1]["sum"] == pytest.approx(220.0)


async def test_entry_unloads_and_closes_the_client(hass, mock_client):
    entry = await setup_entry(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    mock_client.close.assert_awaited_once()
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_the_poll_schedule_fires_at_the_configured_hours(hass, mock_client):
    """The integration must refresh on its own without a periodic interval."""
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await setup_entry(hass)
    calls_after_setup = mock_client.async_get_data.await_count

    now = dt_util.now()
    target = now.replace(hour=POLL_HOURS[0], minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    async_fire_time_changed(hass, target)
    await hass.async_block_till_done()

    assert mock_client.async_get_data.await_count > calls_after_setup


async def test_a_failing_first_poll_leaves_the_entry_retrying(hass):
    """A portal outage at setup must be retryable, not a hard failure."""
    from custom_components.curgasnatural.api import CurGasNaturalConnectionError

    client = AsyncMock()
    client.async_get_data = AsyncMock(side_effect=CurGasNaturalConnectionError("down"))
    client.close = AsyncMock()

    with patch(
        "custom_components.curgasnatural.coordinator.CurGasNaturalClient",
        return_value=client,
    ):
        entry = MockConfigEntry(
            domain=DOMAIN, data=ENTRY_DATA, unique_id=TEST_CONTRACT_ID
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_bad_credentials_at_setup_trigger_reauth(hass):
    from custom_components.curgasnatural.api import CurGasNaturalAuthError

    client = AsyncMock()
    client.async_get_data = AsyncMock(side_effect=CurGasNaturalAuthError("bad"))
    client.close = AsyncMock()

    with patch(
        "custom_components.curgasnatural.coordinator.CurGasNaturalClient",
        return_value=client,
    ):
        entry = MockConfigEntry(
            domain=DOMAIN, data=ENTRY_DATA, unique_id=TEST_CONTRACT_ID
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_energy_statistic_is_imported_alongside_the_volume_one(hass, mock_client):
    """A euro-per-kWh tariff needs a kWh series, not just m³."""
    from homeassistant.components.recorder.statistics import statistics_during_period

    await setup_entry(hass)
    await async_wait_recording_done(hass)

    volume_id = statistic_id_for(TEST_CONTRACT_NUMBER)
    energy_id = energy_statistic_id_for(TEST_CONTRACT_NUMBER)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {volume_id, energy_id},
        "day",
        None,
        {"state", "sum"},
    )

    assert set(stats) == {volume_id, energy_id}
    volume, energy = stats[volume_id], stats[energy_id]
    assert len(energy) == len(volume) == 4
    # Every energy point is its volume point scaled by the configured factor.
    for v, e in zip(volume, energy, strict=True):
        assert e["sum"] == pytest.approx(v["sum"] * 11.20808, rel=1e-4)
        assert e["state"] == pytest.approx(v["state"] * 11.20808, rel=1e-4)
    assert energy[-1]["sum"] == pytest.approx(220.0 * 11.20808, rel=1e-4)


async def test_energy_sensors_use_the_configured_factor(hass, mock_client):
    await setup_entry(hass)

    index = hass.states.get("sensor.r_example_1_2_3_meter_index_energy")
    assert index is not None
    # Energy figures are rounded to 2 dp, matching how the invoice states them.
    assert float(index.state) == round(320.0 * 11.20808, 2)
    assert index.attributes["device_class"] == "energy"
    assert index.attributes["unit_of_measurement"] == "kWh"
    assert index.attributes["conversion_factor"] == 11.20808

    delta = hass.states.get(
        "sensor.r_example_1_2_3_consumption_since_previous_reading_energy"
    )
    assert delta is not None
    assert float(delta.state) == round(20.0 * 11.20808, 2)


async def test_changing_the_factor_reloads_the_entry(hass, mock_client):
    """Options are read at setup, so an edit must trigger a reload."""
    entry = await setup_entry(hass)

    hass.config_entries.async_update_entry(
        entry, options={CONF_CONVERSION_FACTOR: 12.5}
    )
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.conversion_factor == 12.5
    index = hass.states.get("sensor.r_example_1_2_3_meter_index_energy")
    assert float(index.state) == round(320.0 * 12.5, 2)


async def test_correcting_the_factor_rewrites_the_whole_energy_history(
    hass, mock_client
):
    """The energy series is derived, so a corrected factor must fix all of it.

    Regression test for the alternative design: accumulating energy independently
    leaves the old factor baked into every point already written, i.e. a visible
    step in the middle of the series that re-polling never repairs.
    """
    from homeassistant.components.recorder.statistics import statistics_during_period

    entry = await setup_entry(hass)
    await async_wait_recording_done(hass)

    energy_id = energy_statistic_id_for(TEST_CONTRACT_NUMBER)
    volume_id = statistic_id_for(TEST_CONTRACT_NUMBER)

    async def series() -> dict[str, list[dict]]:
        return await hass.async_add_executor_job(
            statistics_during_period,
            hass,
            dt_util.utc_from_timestamp(0),
            None,
            {volume_id, energy_id},
            "day",
            None,
            {"state", "sum"},
        )

    before = await series()
    assert before[energy_id][-1]["sum"] == pytest.approx(220.0 * 11.20808, rel=1e-4)

    # The user finds the real factor on their invoice and corrects it.
    hass.config_entries.async_update_entry(
        entry, options={CONF_CONVERSION_FACTOR: 12.5}
    )
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    after = await series()
    energy, volume = after[energy_id], after[volume_id]

    # Every point, not just the last, now reflects the new factor.
    assert len(energy) == len(volume)
    for v, e in zip(volume, energy, strict=True):
        assert e["sum"] == pytest.approx(v["sum"] * 12.5, rel=1e-6)
    # And the volume series is untouched by the factor change.
    assert [v["sum"] for v in volume] == [v["sum"] for v in before[volume_id]]


async def test_cost_statistic_is_imported_from_the_invoices(hass, mock_client):
    """HA refuses a price entity on an external statistic, so cost needs its own."""
    from homeassistant.components.recorder.statistics import statistics_during_period

    await setup_entry(hass)
    await async_wait_recording_done(hass)

    cost_id = cost_statistic_id_for(TEST_CONTRACT_NUMBER)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {cost_id},
        "day",
        None,
        {"state", "sum"},
    )

    series = stats.get(cost_id)
    assert series, f"no cost statistics under {cost_id}: {list(stats)}"
    # Three invoices in the fixture: 99.99 (2024), 25.67, 12.34.
    assert len(series) == 3
    assert series[-1]["sum"] == pytest.approx(99.99 + 25.67 + 12.34)
    assert series[-1]["state"] == pytest.approx(12.34)


async def test_cost_statistic_is_registered_in_the_local_currency(hass, mock_client):
    """A cost statistic in the wrong currency is rejected as a cost source."""
    from homeassistant.components.recorder.statistics import list_statistic_ids

    await setup_entry(hass)
    await async_wait_recording_done(hass)

    ids = await hass.async_add_executor_job(list_statistic_ids, hass)
    cost = next(
        x
        for x in ids
        if x["statistic_id"] == cost_statistic_id_for(TEST_CONTRACT_NUMBER)
    )

    assert cost["statistic_id"].startswith("curgasnatural:cost_")
    # ``list_statistic_ids`` reports the unit under these two keys, not
    # ``unit_of_measurement``.
    assert cost["statistics_unit_of_measurement"] == hass.config.currency
    assert cost["display_unit_of_measurement"] == hass.config.currency
    assert cost["has_sum"] is True


async def test_a_second_poll_does_not_duplicate_the_cost(hass, mock_client):
    from homeassistant.components.recorder.statistics import statistics_during_period

    await setup_entry(hass)
    await async_wait_recording_done(hass)

    coordinator = next(iter(hass.data[DOMAIN].values()))
    await coordinator.async_refresh()
    await async_wait_recording_done(hass)

    cost_id = cost_statistic_id_for(TEST_CONTRACT_NUMBER)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {cost_id},
        "day",
        None,
        {"sum"},
    )

    series = stats[cost_id]
    assert len(series) == 3
    assert series[-1]["sum"] == pytest.approx(138.00)
