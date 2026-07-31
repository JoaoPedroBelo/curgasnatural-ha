# CUR Gás Natural — Home Assistant Integration

[![Tests](https://img.shields.io/github/actions/workflow/status/JoaoPedroBelo/curgasnatural-ha/tests.yml?style=for-the-badge&label=Tests)](https://github.com/JoaoPedroBelo/curgasnatural-ha/actions/workflows/tests.yml)
[![HACS Validation](https://img.shields.io/github/actions/workflow/status/JoaoPedroBelo/curgasnatural-ha/validate.yml?style=for-the-badge&label=HACS)](https://github.com/JoaoPedroBelo/curgasnatural-ha/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/JoaoPedroBelo/curgasnatural-ha?style=for-the-badge)](https://github.com/JoaoPedroBelo/curgasnatural-ha/releases)
[![License](https://img.shields.io/github/license/JoaoPedroBelo/curgasnatural-ha?style=for-the-badge)](LICENSE)
[![Maintainer](https://img.shields.io/badge/Maintainer-%40JoaoPedroBelo-blue?style=for-the-badge)](https://github.com/JoaoPedroBelo)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JoaoPedroBelo&repository=curgasnatural-ha&category=integration)

---

A Home Assistant custom integration for the **CUR Gás Natural** customer portal
(`portal.curgasnatural.pt`) — the Portuguese natural-gas *Comercializador de
Último Recurso* (supplier of last resort, regulated market).

The portal has no published API, but it is a **SAP Commerce Cloud** storefront, so
this integration authenticates with OAuth2 (authorization code + PKCE) and reads
the same typed **OCC v2 REST API** the portal's own front end uses — no HTML
scraping.

## ✨ Features

- **🔥 Meter readings**: cumulative meter index, consumption since the previous
  reading, and where each reading came from (distributor or client)
- **⚖️ Both units**: every volume is mirrored in energy — the meter counts m³ but
  the supplier bills kWh, so both are published as sensors *and* as statistics
- **📊 Gas dashboard ready**: every reading imported as a long-term statistic,
  placed on the day the meter was actually read
- **🧾 Billing**: latest invoice total and due date, amount still owed, total
  billed over the last 12 months, plus pending-payment and failed-direct-debit
  alerts — and a **cost statistic** built from real invoiced totals, so the Gas
  dashboard shows money that matches the bill
- **📅 Next reading window**: the dates the distributor is expected to read
- **🏠 Multi-contract**: one entry per delivery point, each with its own device
  and statistic
- **🕐 Low-profile polling**: twice a day (02:00 & 14:00) — no tight loops
- **🔐 No two-factor friction**: the portal does not challenge new sessions, so
  polling stays fully headless
- **🇬🇧🇵🇹 Localised**: English and Portuguese

## 🚀 Quick Start

### Installation via HACS

1. Open HACS in your Home Assistant instance
2. Click on "Integrations"
3. Click the three dots in the top right corner and select "Custom repositories"
4. Add this repository URL: `https://github.com/JoaoPedroBelo/curgasnatural-ha`
5. Select category "Integration"
6. Click "Add"
7. Find "CUR Gás Natural" in HACS and click "Install"
8. Restart Home Assistant

### Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "CUR Gás Natural"
4. Enter your portal credentials:
   - **E-mail**: the address you sign into `portal.curgasnatural.pt` with
   - **Password**: your portal password
5. If your account holds more than one contract, pick the delivery point this
   entry should track. **Add the integration again for each further contract.**

The integration validates your credentials with a live login before creating the
entry, then refreshes automatically **twice a day (02:00 and 14:00 local time)**.

## 🎯 Entities

### Sensors (12)

| Entity | Unit | Notes |
|--------|------|-------|
| **Meter index** | m³ | The cumulative meter reading. Attributes: reading date, origin, CUI, contract number |
| **Meter index (energy)** | kWh | The same reading × the network's conversion factor — what the supplier bills |
| **Consumption since previous reading** | m³ | Delta between the last two readings. Attributes: period start/end, days |
| **Consumption since previous reading (energy)** | kWh | The same delta in billed energy |
| **Last reading date** | date | Attribute: origin (`Leitura do distribuidor` / `Leitura do cliente`) |
| **Next reading window start** | date | When the distributor is next expected |
| **Next reading window end** | date | |
| **Last invoice total** | € | Attributes: invoice number, payment status, billing period |
| **Last invoice due date** | date | |
| **Amount due** | € | Sum of every invoice still pending payment |
| **Billed last 12 months** | € | Rolling total of invoices issued in the last year |
| **Contract status** | — | Diagnostic. Attributes: contract number, CUI, tier, distributor |

### Binary sensors (3)

- **Invoice pending payment** — ON while any invoice is unpaid
- **Direct debit failed** — ON if any invoice records a failed direct debit
- **Service available** — ON when the last poll succeeded (diagnostic, disabled by default)

> The portal exposes a real cumulative meter index, but the readings are
> **backdated and sparse** — a reading dated the 20th surfaces days later, and the
> distributor reads roughly monthly. A live `total_increasing` sensor would pile a
> whole month's gas onto the poll hour, so the **Gas dashboard is fed by the
> `curgasnatural:consumption_<contract>` long-term statistic** instead, with each
> reading timestamped at its own local midnight.

## ⚖️ m³ vs kWh: set your conversion factor

The meter counts **m³**, but your supplier bills **kWh**, converting with the
calorific value (PCS) of your distribution network. That factor is printed on
every invoice next to the reading — `4 m³ x 11.20808 = 45 kWh`.

No endpoint on the portal exposes it, so it is a setting: **Settings → Devices &
Services → CUR Gás Natural → Configure**. It defaults to `11.2`, which is close
to the Portuguese networks' typical value but **not** exact for yours — copy the
number from your invoice.

Changing it applies to readings imported *afterwards*; already-imported statistics
keep the factor that was in force when they were written.

## 📊 Adding it to the Gas dashboard

**Settings → Dashboards → Energy → Gas consumption → Add gas source**, then pick
one of the two series this integration publishes:

| Statistic | Unit | Pick this when |
|-----------|------|----------------|
| **CUR Gás Natural Energy (*contract*)** | kWh | The usual choice — it is the unit your supplier bills in |
| **CUR Gás Natural Consumption (*contract*)** | m³ | You want the dashboard to show what the meter physically counted |

For the **cost** field, pick **CUR Gás Natural Cost (*contract*)**. Home Assistant
does not accept a price entity on an external statistic ("Use stat_cost instead"),
so this integration publishes cost as its own statistic — built from the totals the
supplier actually invoiced. That is strictly better than price × consumption,
because it already includes the fixed daily terms, the ISP/CO₂ levies and VAT.

Each invoice is booked at the **end of the period it bills**, so cost lines up with
the consumption that produced it.

⚠️ **Add only one consumption statistic.** Both describe the same gas, so
configuring both double-counts your consumption. If you already have a hand-rolled gas source for the same meter
(a template sensor plus manual readings, say), replace it rather than adding
alongside — and prefer this integration's statistic, because a `total_increasing`
template sensor whose statistics you also inject by hand will collide with the
recorder's own compilation and can show negative consumption after a restart.

Consumption shows as one bar per meter reading, covering the period since the
previous one — that is the full resolution this API offers.

## 🏷️ Entity IDs (read this before writing automations)

Entities are named after their **device**, and the device is named after the supply
address. So the entity IDs are built from that address, not from the integration
name:

```text
device:     R. EXAMPLE 1, 2, 3
entity_id:  sensor.r_example_1_2_3_meter_index
            sensor.r_example_1_2_3_consumption_since_previous_reading
            sensor.r_example_1_2_3_amount_due
            binary_sensor.r_example_1_2_3_invoice_pending_payment
```

That keeps several contracts unambiguous, but it has two consequences worth knowing:

- **Your street address ends up inside every entity ID**, so it will appear in any
  dashboard, automation or screenshot you share.
- The examples below use `<device>` as a placeholder. Substitute your own prefix, or —
  better — **rename the device once** (Settings → Devices & Services → the device →
  ✏️) and let Home Assistant rewrite every entity ID for you. Renaming it to, say,
  `Gas` gives you `sensor.gas_meter_index`.

## 🏗️ Example Automations

Replace `<device>` with your device's slug (see above).

### Remind me before the reading window

```yaml
automation:
  - alias: "Submit a gas meter reading"
    trigger:
      - platform: template
        value_template: >
          {{ as_timestamp(states('sensor.<device>_next_reading_window_start'))
             - as_timestamp(now()) < 86400 }}
    action:
      - service: notify.mobile_app
        data:
          title: "Gas reading window opens tomorrow 🔥"
          message: >
            Current index: {{ states('sensor.<device>_meter_index') }} m³.
```

### Alert on an unpaid invoice

```yaml
automation:
  - alias: "Notify on unpaid gas invoice"
    trigger:
      - platform: state
        entity_id: binary_sensor.<device>_invoice_pending_payment
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          title: "Gas invoice pending 🧾"
          message: >
            {{ states('sensor.<device>_amount_due') }} € due by
            {{ states('sensor.<device>_last_invoice_due_date') }}.
```

### Flag an unusually high period

```yaml
automation:
  - alias: "High gas consumption"
    trigger:
      - platform: numeric_state
        entity_id: sensor.<device>_consumption_since_previous_reading
        above: 20   # m³ since the previous reading
    action:
      - service: notify.mobile_app
        data:
          message: >
            {{ states('sensor.<device>_consumption_since_previous_reading') }} m³ over
            {{ state_attr('sensor.<device>_consumption_since_previous_reading', 'days') }}
            days.
```

## 📖 Documentation

- **[Architecture](custom_components/curgasnatural/docs/ARCHITECTURE.md)**: components, data flow, token lifecycle
- **[API](custom_components/curgasnatural/docs/API.md)**: the reverse-engineered OAuth2 handshake and verified response shapes

## 🛠️ Technical Details

- **Service**: CUR Gás Natural customer portal (`portal.curgasnatural.pt`), on SAP Commerce Cloud (OCC v2, base site `galpcurarea`)
- **Auth**: OAuth2 authorization code + PKCE (S256), public client, ~12 h access token with refresh
- **Architecture**: `DataUpdateCoordinator`; all network logic isolated in `api.py`
- **Integration Type**: Service (Cloud Polling)
- **Update Schedule**: Twice daily (02:00 / 14:00) plus once on startup
- **Home Assistant**: Compatible with 2024.1.0+

## 🤝 Contributing

Contributions are welcome. If your CUR account exposes endpoints this integration
does not yet read (see [docs/API.md](custom_components/curgasnatural/docs/API.md)),
that directly unlocks more entities — please open an issue with the *shape* of the
payload, never the values.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 👤 Author

**João Belo** ([@JoaoPedroBelo](https://github.com/JoaoPedroBelo))

## ⚠️ Disclaimer

This is an independent, open-source integration for the CUR Gás Natural customer
portal. It is not affiliated with, endorsed by, or sponsored by Galp, Lisboagás,
any other CUR entity, or SAP. Use it in accordance with the portal's terms of
service.

The CUR logo in `custom_components/curgasnatural/brand/` is the property of its
owner and is reproduced here nominatively, solely to identify the service this
integration connects to. It is not covered by this project's MIT licence.

## 🔒 Privacy

Your portal **e-mail** and **password** are stored only in your local Home
Assistant config entry and are sent solely to `curgasnatural.pt`. The contract
payload also carries your NIF, IBAN and phone number: these are never logged and
never exposed as entity state.

Two identifiers *are* surfaced locally, by design: the **CUI** (delivery point) as a
sensor attribute and the device serial number, and the **supply address** as the
device name — which means it also appears in every entity ID (see
[Entity IDs](#-entity-ids-read-this-before-writing-automations)). Rename the device
if you would rather it did not.

Never commit real credentials, CUI, contract numbers or captured payloads to this
repository.

## 🐛 Issues & Support

- [GitHub Issues](https://github.com/JoaoPedroBelo/curgasnatural-ha/issues)
- Review Home Assistant logs for error messages

## ⭐ Show Your Support

If you find this integration useful, please consider giving it a star on GitHub!
