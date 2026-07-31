# CUR Gás Natural — Home Assistant Integration

Home Assistant custom integration for the **CUR Gás Natural** customer portal
(`portal.curgasnatural.pt`) — Portugal's regulated-market natural gas supplier of
last resort.

**Key concept:** the portal is a **SAP Commerce Cloud** storefront, so there *is*
a typed API — OCC v2 — behind an OAuth2 authorization server. Home Assistant
authenticates with **authorization code + PKCE** and calls the same endpoints the
portal's Angular front end calls. No HTML scraping.

## Documentation-first

Read the relevant doc in `custom_components/curgasnatural/docs/` **before**
changing the API client, coordinator, or entities:

| Doc | Read when |
|-----|-----------|
| `ARCHITECTURE.md` | Component relationships, data flow, token lifecycle |
| `API.md` | The reverse-engineered requests, response shapes, header rules |

## Core files

| File | Purpose |
|------|---------|
| `api.py` | **All portal network logic.** Private cookie jar, PKCE handshake, token refresh, OCC calls. |
| `coordinator.py` | `DataUpdateCoordinator`; normalises the raw payloads into `self.data`. Twice-daily schedule. |
| `statistics.py` | Imports each meter reading as the `curgasnatural:consumption_<contract>` external statistic (Gas dashboard source). |
| `entity.py` | Shared entity base: unique ids and the per-contract device. |
| `const.py` | `Final`-typed constants: config keys, hosts, endpoints, entity keys, `coordinator.data` keys, `POLL_HOURS`. |
| `config_flow.py` / `__init__.py` | Config UI (live login + contract picker) / entry point. |
| `sensor.py` / `binary_sensor.py` | Entities, declared as description tables. |

## Critical rules

1. **All portal network logic in `api.py`.** The coordinator never talks HTTP
   directly.
2. **All state in the coordinator** — entities read
   `self.coordinator.data.get(...)` and return `None` for missing values.
3. **The client owns its own cookie jar** — never the shared HA session. The PKCE
   handshake is stateful, so cookies are cleared before each login.
4. **Do not change the per-step request headers without re-verifying live.** The
   authorization server's CORS filter fails the *next* request: sending `Origin`
   on `/authorize` breaks `/csrf` with `403 Invalid CORS request`, and omitting
   `Referer` on `/csrf` yields `400 Login page configuration does not match`. See
   `docs/API.md`.
5. **`/authorize` must run before `/csrf`** — it is what creates the OAuth
   session.
6. **Poll twice a day (02:00 / 14:00)** via `async_track_time_change` — no tight
   periodic loop.
7. **Never commit or log credentials, tokens, or the CUI/NIF/IBAN** the contract
   payload carries. Use placeholders in tests and docs.
8. **Log via `_LOGGER`** — never `print()` (ruff `T20`).
9. Refresh, then re-login, then fail — once each.
10. A statistics failure must never fail the poll.

## Data notes

- `gv` is the **cumulative meter index in m³**, as a string, sometimes padded.
- Reading dates are **day-first** `dd-mm-yyyy`; reading windows are `yyyymmdd`;
  invoice dates carry a `+0000` offset **without a colon**.
- The distributor and the client can report the **same day** — collapse duplicates.
- Readings are backdated and sparse, hence the statistic rather than a
  `total_increasing` sensor.

## Development

Prefer the project venv directly (`.venv/bin/...`):

```bash
.venv/bin/ruff check custom_components/ tests/ scripts/    # lint (blocks CI)
.venv/bin/ruff format custom_components/ tests/ scripts/   # format
.venv/bin/mypy custom_components/curgasnatural             # types (advisory in CI)
.venv/bin/pytest tests/ -q                                 # tests
```

Or via Make: `make lint`, `make format`, `make test`, `make coverage`, `make check`.

The venv must be **Python 3.11** — Home Assistant does not support 3.14.

`mypy` reports one known false positive on `config_flow.py`
(`Unexpected keyword argument "domain"`): it cannot see HA's
`__init_subclass__(domain=...)`. This is why types are advisory in CI.

## Testing

**Tests are mandatory** for every new sensor, endpoint, or normalisation change —
use the `raw_payload` / `mock_coordinator` / `mock_config_entry` fixtures in
`tests/conftest.py`.

Two modules earn their keep beyond ordinary unit coverage:

- **`tests/test_init.py`** boots a **real Home Assistant**, adds a config entry and
  asserts the entities and the long-term statistic actually materialise. This is what
  catches the failures unit tests cannot see: illegal `device_class`/`state_class`
  pairs, recorder wiring, and the entity IDs HA really composes. Keep it passing.
- **`tests/test_entity_descriptions.py`** validates every description against HA's
  own `DEVICE_CLASS_STATE_CLASSES` / `DEVICE_CLASS_UNITS` tables, so an illegal
  combination fails in CI instead of at runtime in someone's house.

Two gotchas in the end-to-end module:

- `recorder_mock` must be built **before** anything pulls in the `hass` fixture
  (`recorder_db_url` asserts hass has not started), which is why one autouse fixture
  requests them in that order.
- The recorder needs `psutil-home-assistant`, `SQLAlchemy` and `fnv-hash-fast`, which
  `pytest-homeassistant-custom-component` does not pull in — they are pinned in
  `requirements-test.txt` for exactly this reason.

`statistics.py` still imports the recorder lazily so the module stays importable
without those extras.

## Brand assets

`custom_components/curgasnatural/brand/` holds the **official CUR logo**, used
nominatively to identify the service this integration talks to — the same basis on
which `home-assistant/brands` carries third-party logos.

- `cur-logo.svg` is the **source of truth**, taken verbatim from
  `https://portal.curgasnatural.pt/curpath/assets/logo/cur-logo.svg`.
- The four PNGs are **generated**: run `make brand`
  (`scripts/generate_brand_assets.py`, needs Pillow + cairosvg). Never hand-edit them.
- `icon.png` is the cyan burst mark alone, cropped at `MARK_WIDTH_UNITS` of the
  viewBox and centred on a transparent square. `logo.png` is the full lockup.

The generator lives outside `custom_components/` on purpose: the HACS release zip
only packs the component directory, so users never download it.

Home Assistant's *official* icon path is a PR to the `home-assistant/brands`
repository — these files are the local copy, not what HA renders in its UI.

The logo is the property of its owner and is **not** covered by this project's MIT
licence; the README disclaimer says so, and that wording should stay.

## Git & releases

- Conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`,
  `perf:`, `ci:`).
- **No AI attribution** anywhere. **No commits unless explicitly asked.**
- Releases: bump `custom_components/curgasnatural/manifest.json`, add a
  `CHANGELOG.md` entry, tag `vX.Y.Z` (the tag must match the manifest version —
  enforced by `release.yml`).
