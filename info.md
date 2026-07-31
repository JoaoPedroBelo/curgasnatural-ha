# CUR Gás Natural Integration

Monitor your regulated-market natural gas contract from the **CUR Gás Natural**
customer portal (`portal.curgasnatural.pt`) in Home Assistant.

The portal has no published API, but it is a SAP Commerce Cloud storefront — so this
integration signs in with OAuth2 (authorization code + PKCE) and calls the same
typed OCC v2 REST API the portal's own front end uses. No HTML scraping.

## Features

- **Meter readings** — the cumulative index, consumption since the previous
  reading, the reading date and whether the distributor or you reported it
- **Both units** — every volume mirrored in energy, because the meter counts m³ but
  your supplier bills kWh. The conversion factor is a setting; correct it and the
  whole history is recomputed
- **Gas dashboard** — three long-term statistics per contract: m³, kWh, and **cost
  built from the totals actually invoiced** (so it includes fixed terms and VAT,
  which price × consumption never would)
- **Billing** — last invoice and its due date, amount still owed, 12-month billed
  total, plus unpaid-invoice and failed-direct-debit flags
- **Next reading window** — the dates the distributor is expected to read
- **Multi-contract** — one entry per delivery point, each with its own device
- **Cloud polling** — twice a day (02:00 & 14:00), no two-factor prompts
- English 🇬🇧 and Portuguese 🇵🇹

## Quick Start

1. Install via HACS and restart Home Assistant
2. **Settings → Devices & Services → Add Integration → CUR Gás Natural**
3. Enter your portal **e-mail** and **password**, then pick the contract
4. Open **Configure** and set the **conversion factor** (kWh/m³) printed on your
   invoice next to the reading — the default of 11.2 is typical but not exact
5. Wire the statistics into **Settings → Dashboards → Energy → Gas consumption**

## Author

**João Belo** — independent, open-source project. Not affiliated with Galp,
Lisboagás or any CUR entity. The CUR logo is the property of its owner and is used
only to identify the service this integration connects to.
