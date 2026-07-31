# CUR Gás Natural Integration

Monitor your regulated-market natural gas contract from the **CUR Gás Natural**
customer portal (`portal.curgasnatural.pt`) in Home Assistant.

## Features

- **Meter readings** — cumulative index (m³) and consumption since the previous
  reading, with the reading's origin
- **Gas dashboard** — every reading imported as a long-term statistic
- **Billing** — last invoice, amount due, 12-month total, unpaid and failed
  direct-debit alerts
- **Next reading window** — when the distributor is expected
- **Multi-contract** — one entry per delivery point
- **Cloud polling** — twice a day (02:00 & 14:00), no two-factor prompts
- English 🇬🇧 and Portuguese 🇵🇹 translations

## Quick Start

1. Install via HACS
2. Add the integration via **Settings → Devices & Services → Add Integration**
3. Enter your portal **e-mail** and **password**, then pick the contract

## Author

**João Belo** — independent, open-source project. Not affiliated with Galp,
Lisboagás or any CUR entity.
