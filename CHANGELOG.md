# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-07-31

### Changed — the login debug line no longer carries your e-mail

`api.py` logged `login successful for <e-mail>` at debug level. Debug logs are
exactly what people paste into bug reports, and the address was not telling the
reader anything the surrounding context did not. The line stays; the e-mail goes.

Nothing else in the integration behaves differently — if you are on 0.2.0 and do
not enable debug logging, this release changes nothing for you.

### Security — every GitHub Action is pinned to a commit SHA

The workflows used moving refs: `hacs/action@main` and
`home-assistant/actions/hassfest@master`, plus major-version tags for the rest. A
moving ref runs whatever upstream publishes at the moment the job starts, and
`release.yml` runs with `contents: write`. All of them are now pinned to a commit
SHA, with the human-readable version kept in a trailing comment.

Pins rot, so Dependabot is configured for `github-actions` and will open a PR when
an action publishes a new release. That is the part that makes pinning sustainable
rather than a slow drift into unpatched actions.

### Repository — history was rewritten on 31 July 2026

The test fixtures and `docs/API.md` carried real readings and invoice amounts from
the author's own account, and a throwaway virtualenv used to reproduce a CI matrix
job locally had been committed by mistake. Both are gone from **every** commit, so
`main`, `v0.1.0` and `v0.2.0` were force-pushed and the release archives rebuilt.

The fixture values are now synthetic and deliberately arbitrary — every derived
assertion was recomputed to match, so changing one in isolation will break tests
across several files. Dates were left alone: they are load-bearing for the
12-month invoice window and the statistics spreading.

**If you cloned or forked before this date**, your history has diverged and you
will need to re-clone. `.gitignore` now covers `.venv*/` rather than a bare
`.venv`, which is what let the virtualenv slip through.

## [0.2.0] - 2026-07-31

### Fixed — a month no longer reports the previous month's gas

The consumption statistic dated each reading's whole delta at the reading day. That
is wrong in a way only the dashboard reveals: the Gas dashboard diffs the running
`sum` at month boundaries, so a reading taken on 11 July carried the ~30 days of gas
since the previous reading — two thirds of it June's — entirely into July's total.
July reported 7 m³ against roughly 4 actually burnt in July.

Consumption is now written as **one point per calendar day**: each reading's delta is
split evenly across the days between two readings, and the meter index is
interpolated for `state`. Reading days still land on the exact index the portal
reported, so no gas is invented or lost — only attributed to the days it was burnt.

An even split *is* an approximation, since gas use is not uniform, but a far smaller
one than misattributing a whole month.

### Changed — the polled window is rewritten, not appended to

Spreading requires the days between two readings to be recomputed whenever either
end moves, so each poll now rewrites the window the portal returns instead of
appending to it. The running total is anchored on what is already stored for the
**oldest** reading in that window, which keeps `sum` monotonic and leaves history
older than the window untouched.

Side effect worth having: a backdated reading arriving late now repairs the days it
covers, instead of dumping its gas on the day it appeared.

The cost series is deliberately *not* split — an invoice is a single event, not a
daily accrual, so it stays dated at the close of the period it bills.

**Upgrading:** the fix applies itself. The first poll after the update rewrites the
whole reading window, so past months correct themselves; nothing has to be deleted
by hand.

## [0.1.0] - 2026-07-31

Initial release.

### Added

- OAuth2 **authorization code + PKCE** client for the CUR Gás Natural portal
  (`api.py`), with refresh-token renewal and a single transparent re-login on a
  rejected bearer token.
- Config flow with a live-login check and a **contract picker** — one config
  entry, device and statistic per delivery point.
- `DataUpdateCoordinator` polling **twice a day (02:00 / 14:00)** via
  `async_track_time_change`, normalising six self-care payloads into flat state.
- 10 sensors: meter index (m³), consumption since the previous reading, last
  reading date, next reading window start/end, last invoice total and due date,
  amount due, billed last 12 months, and contract status (diagnostic).
- 3 binary sensors: invoice pending payment, direct debit failed, service
  available (diagnostic, disabled by default).
- **Gas dashboard support** through the `curgasnatural:consumption_<contract>`
  external statistic, importing each meter reading at its own local midnight so
  consumption lands on the day the meter was read.
- English and Portuguese translations.
- Reverse-engineering notes in `docs/API.md` and `docs/ARCHITECTURE.md`, including
  the header discipline the authorization server's CORS filter requires.

### Added — volume *and* energy

The meter counts m³ but the supplier bills kWh, so every volume figure is now
mirrored in energy:

- Two new sensors: **Meter index (energy)** and **Consumption since previous
  reading (energy)**, both in kWh.
- A second statistic per contract, `curgasnatural:energy_<contract>` (kWh),
  alongside `curgasnatural:consumption_<contract>` (m³). Wire **one** of them into
  the Gas dashboard — the kWh one if your tariff is in €/kWh, which is the usual
  case. Adding both double-counts.
- An options flow for the **conversion factor** (kWh/m³), the network's calorific
  value printed on every invoice next to the reading. No portal endpoint exposes
  it, so it is a setting; it defaults to 11.2 and is bounded to 8–14 to catch
  typos.

The energy series is **derived from the volume series on every poll**, not
accumulated alongside it. That is what makes correcting the factor repair the whole
history: an independently accumulated series would keep the old factor baked into
every point already written, leaving a step in the middle that re-polling never
fixes.

### Added — cost from real invoices

Home Assistant refuses `entity_energy_price` / `number_energy_price` on an external
statistic ("Use stat_cost instead"), so a Gas dashboard fed by this integration had
no way to show money. It now publishes a third statistic,
`curgasnatural:cost_<contract>`, in the instance's currency:

- built from the **totals the supplier actually invoiced**, so it already includes
  the fixed daily terms, ISP/CO₂ levies and VAT that price × consumption misses;
- each invoice booked at the **end of the period it bills**, lining cost up with the
  consumption that produced it, and invoices closing on the same day merged;
- append-only, with a non-positive total (a credit note) clamped to zero — a falling
  `sum` reads as a meter reset to Home Assistant and renders as a large negative bar.

### Verified

Installed and exercised against a live Home Assistant **2026.7.4** and a real CUR
account: config flow (login + contract picker), 13 entities, the imported statistic
(20 reading points, cumulative 131 m³), a second poll that does not double-count,
entry reload and unload — with no errors, warnings or deprecations in the log.

The three defects that only surfaced under a real Home Assistant, all fixed before
this release:

- `device_class: gas` was paired with `state_class: measurement` on the
  period-consumption sensor. Home Assistant only accepts `total`/`total_increasing`
  for gas, so the entity was rejected at runtime. That sensor now declares no device
  class — it is a delta between two past readings, not a meter.
  `tests/test_entity_descriptions.py` now validates every description against HA's
  own `DEVICE_CLASS_STATE_CLASSES` / `DEVICE_CLASS_UNITS` tables.
- The config flow asked for the portal password with a bare `str`, which Home
  Assistant renders as a **visible** text box. Both fields now use `TextSelector`,
  so the password is masked and browsers offer the right autofill.
- The README's automation examples used invented entity IDs. Entities are named
  after their device, and the device after the supply address, so the real IDs look
  like `sensor.r_example_1_2_3_meter_index`. Documented, with the caveat that this
  puts the supply address into every entity ID.

[0.2.1]: https://github.com/JoaoPedroBelo/curgasnatural-ha/releases/tag/v0.2.1
[0.2.0]: https://github.com/JoaoPedroBelo/curgasnatural-ha/releases/tag/v0.2.0
[0.1.0]: https://github.com/JoaoPedroBelo/curgasnatural-ha/releases/tag/v0.1.0
