🇮🇹 [Italiano](README.md) · 🇬🇧 **English**

> **Project maintained by [Skacco](https://github.com/Skacco).** Built on the work of [Pitscheider](https://github.com/Pitscheider/ha-inim-prime) (GPL-3.0), author of the original integration and of the panel communication libraries. This project adds: the **Native mode** (local TCP protocol, fast), **outputs** (switches; dimmable ones as lights only in PrimeLAN mode), **scenarios** (buttons + active scenario sensor), an **alarm panel** driven by scenarios or partitions (with an optional second **PIN**-protected panel), **timers**, **thermostats** (temperature sensors), **roller shutters** (driven by virtual outputs), **realtime push** (local webhook, optional), device grouping and the **Italian interface**.

# Introduction
This custom integration lets Home Assistant communicate with **Inim Prime alarm panels**.

## Integration modes
You can use two integration modes, **separately or together**. You choose during the integration setup.

### Native — local protocol (TCP)
Talks **directly to the panel** on the local protocol port (TCP, default **6004**), with AES encryption. It is the **fast, fully local** channel: it does not go through the cloud or the Web API.
It exposes:
- **zones** (open/closed state, alarm memory, bypass)
- **partitions** (state, arm/disarm, alarm memory)
- **outputs** (switches)
- **timers**
- **thermostats** (temperature sensors)
- **roller shutters** (driven through the panel's command outputs)

It only needs the local protocol port to be reachable and its password (default `pass`). It does **not** need the PrimeLAN board.

### PrimeLAN — official Inim Web API
Uses the **official Inim Web API** exposed by the **PrimeLAN** network board. It adds the features the local protocol does not expose:
- **scenarios** (one button per scenario + "Active scenario" sensor)
- **GSM status** (operator, signal strength, credit)
- **system faults**
- the panel's **event log**

It requires the **PrimeLAN network board** installed in the panel.

### Both (recommended)
You enable the two integration modes together: **Native** does the fast local work (zones, partitions, outputs, shutters, thermostats, timers), **PrimeLAN** adds scenarios, GSM, faults and the log. The Home Assistant **alarm panel** joins the two worlds: it reads the state from the partitions and sends arm/disarm commands through the PrimeLAN **scenarios**.

## Firmware compatibility
Tested with panels on both **firmware 4.x** and **firmware 5.x**.

The project is **community developed**, **not affiliated with INIM Electronics**, and is meant to **complement — not replace** the official Inim software.

# Disclaimer
This integration is provided **as is** and interacts with a **security/alarm system**.
Use it **at your own risk** and **do not rely on it as the only way to control or monitor** your panel.
The author **accepts no responsibility** for:
- Malfunctions or unexpected behaviour
- Missed or delayed events
- Wrong panel states
- Any damage, loss or security issue arising from the use of the integration
- Unauthorized access

Always verify critical operations directly on the Inim Prime panel or through the official Inim software.

# Security notes
- PrimeLAN's HTTPS implementation uses a dated TLS.
- It is strongly recommended to use this integration **only on a trusted LAN**.
- Never expose the panel directly to the Internet.

> **Before installing**, if you haven't already, set up the panel with Prime/STUDIO: see **[Inim Prime panel setup](#inim-prime-panel-setup)** at the bottom of this page.

# Installation in Home Assistant
## From the Marketplace (recommended)
This is the recommended way because it handles updates. The integrations store is now built into Home Assistant (Marketplace), so there is nothing else to install.
### Add the custom repository
Open the **Marketplace** → top-right menu (three dots) → `Custom repositories`.
In the `Repository` field paste `https://github.com/Skacco/ha-inim-prime`, choose `Integration` as the category, press `ADD`.
### Install the integration
Search for `Inim Prime`, open it and press `DOWNLOAD`. On the **first install no restart is needed**: you can add the integration right away (see below). A restart is only required for later **updates**.
## Manual installation (not recommended)
Download the source code of the latest release from [https://github.com/Skacco/ha-inim-prime](https://github.com/Skacco/ha-inim-prime), copy the `inim_prime` folder into `custom_components` of your Home Assistant configuration and restart.

# Integration setup in Home Assistant
Go to `Settings` → `Devices & services` → `Add integration`, search for `Inim Prime`.

### Choosing the integration mode
At the start you choose which integration mode to use: **Native only**, **PrimeLAN only** or **Native + PrimeLAN**.

### Initial setup
- **Serial number** — paste it (the one copied from Prime/STUDIO). It is not verified by the integration and **cannot be changed later**: it identifies the installation and determines where data is stored. If you don't have it, an alternative is the network MAC.
- **Host** — the panel's IP or hostname (reserve a static IP or a DNS record if you use DHCP).
- **Native** section (if enabled): port (default 6004), protocol password (default `pass`), optional custom PIN.
- **PrimeLAN** section (if enabled): `Use HTTPS` (recommended) and the `API Key` generated in Prime/STUDIO.

The first setup asks for **the connection only**. Scan intervals, alarm panel, roller shutters and the other parameters start with default values and can be adjusted at any time from the **Options** (see below) — where scenario/partition names and shutter modules are already loaded.
The first setup can take a while: that is normal, the Inim Prime panel is not very fast 😅.

### End of setup
At the end you will see the devices created by the integration, which you can rename and assign to an Area. Press `Finish`.

# Reconfiguration
To change host, HTTPS, API key, port/password/PIN or to change the integration mode: open the integration, click the three dots on the device (default name `INIM Prime (serial_number)`) and press `Reconfigure`.

## After changes on the panel
When you change the panel configuration (add/rename **zones, outputs, partitions, scenarios, roller shutters**, etc.) the new entities do **not** appear on their own: after you `Upload` from Prime/STUDIO, in HA open the integration → ⋮ menu → **Reload** (or restart Home Assistant). On reload the integration re-reads the panel and creates/updates the entities.

# Options
Click the gear icon next to the panel. You can configure:

## Panel log events fetch limit
Maximum number of events downloaded when new logs are detected. The default value is recommended; the integration automatically downloads more if needed. It is not real time (fetching is asynchronous).

## Scan intervals
How often Home Assistant polls the panel (lower values = more traffic). Each field shows the **integration mode** used: **Zones / Partitions / Outputs** are read from **Native** if configured (local, fast), otherwise from **PrimeLAN**; **GSM / Faults / Log events / Scenarios** are **PrimeLAN only**.

⚠️ The **Native** mode supports much lower intervals than PrimeLAN for zones, partitions and outputs (local polling).

ℹ️ When you use **both** integration modes, "live" data comes from Native, so the **PrimeLAN-only** pollers start with a **slower default (60s for GSM, faults and log events)** to reduce PrimeLAN traffic; **scenarios** stay more responsive because they drive the alarm panel. You can still change every value in the options.

## Alarm panel
The integration creates an `alarm_control_panel` entity to arm/disarm the system. A command can be executed in **two ways**, which differ in one important thing: what happens with **open zones/windows**.

- **Scenario** (through PrimeLAN): activates a panel **scenario**. The scenario applies the **panel's checks**, so with an open zone that is not auto-bypassable it **refuses to arm and warns you**. It is the equivalent of sdomotica's behaviour.
- **Partitions** (through Native): acts **directly on the partitions**, **forcing the arming** (it bypasses the scenario check). Auto-bypassable zones are bypassed; non auto-bypassable ones are still reported/blocked by the panel. It is **almost instant** (all local). It lets you **mix modes** (some partitions in Total, others in Instant in the same command), which many scenarios don't do.

### How to configure it
The options open with a **menu**: *General parameters*, *Scan intervals*, *Alarm panel*, — **only if the panel has shutter modules** — *Roller shutters*, and **💾 Save and exit**. Each section edits an in-memory copy and goes back to the menu; **nothing is written until you press “💾 Save and exit”**. The **X** at the top closes without saving. So there is one single save and one single cancel: going back never saves anything halfway.

In *Alarm panel* (with scenarios available, i.e. with PrimeLAN) there is **one single screen with one group per command**, with the same names as Home Assistant and in the same order (*Home* / *Away* / *Night* / *Vacation* / *Disarmed*). Each group has:
- a **dropdown**: the **panel scenarios by name**, or **Custom partitions…** (only if the Native mode is also enabled); empty (the X) = command not offered in the panel;
- right below, a **collapsed section** “↳ Vacation: custom partitions and outputs” with **one row per partition** (*Disarmed* / Total / Partial / Instant) plus **Output activation**. It applies **only** if you picked *Custom partitions…* in the dropdown; otherwise it is ignored.

You immediately see what is set for all commands and do everything on one screen. Press **“Next”** to go back to the menu, where you save with **💾 Save and exit**. A partition has **one single mode** per command (a double state is impossible). Note: Home Assistant does not allow naming a form button “Save”, which is why the forms use “Next”.

Typical example (both modes):
- **Away** → scenario *Day armed* (keep the open-windows warning for daily use)
- **Night** → scenario *Night armed*
- **Vacation** → *Custom partitions…*, then in the section below, the mode for each partition (e.g. perimeter *Total*, indoor motion *Instant*, the others *Disarmed*) — it forces arming with a mix scenarios can't replicate
- **Disarmed** → *➜ All partitions disarmed* (recommended: it is the fastest command) or the scenario *Off*

> **Tip for disarming.** With both modes active, for **Disarmed** pick **“➜ All partitions disarmed”** rather than the scenario *Off*: the command goes through the Native protocol instead of PrimeLAN and is noticeably faster (about 2 seconds versus 4.5–6 in testing). The scenario *Off* is only needed if you tied other actions to it on the panel.

> Native only → there are no scenarios: *Alarm panel* shows the list of commands and each one opens the partitions form directly. PrimeLAN only → the dropdowns contain scenarios only.

Partitions not listed stay disarmed. The **state** shown in HA: a partition in alarm → *triggered*; otherwise, if a partitions command matches the current state exactly it shows that, otherwise the active scenario applies, otherwise *disarmed*/*custom bypass*.

**Output activation.** A partitions command can also activate **one or more outputs** (e.g. "Close house" to close the shutters); the panel releases monostable outputs by itself. For scenario commands the scenario itself takes care of it, so the outputs field only applies when the command uses partitions.

**Auto-bypassable zones.** With the PrimeLAN mode a scenario is not activated if an auto-bypassable zone is open. If you use auto-bypassable zones, configure that command with *Custom partitions…* (requires the Native mode): with PrimeLAN alone there is no alternative.

### Which panel is created
- **Native only** → **partition** blocks (no Scenario field).
- **PrimeLAN only** → **scenario** mapping (you pick the scenario per command from a dropdown with the names; without a disarm scenario the panel is not created).
- **Both integration modes** → **one single hybrid panel**: for each command you choose scenario or partitions with the rule above.

### Alarm panel with PIN (optional, for a wall tablet)
If you keep an always-on tablet in a free-access room, you can add a **second PIN-protected panel**, leaving the original one without a PIN for phone use.

Enable it in Options → *General parameters* → **Extra alarm panel with PIN** + the **PIN** field. The integration creates a second `alarm_control_panel.…_pin` entity that:
- reflects the **same state** and uses the **same commands** as the normal panel;
- asks for the **PIN only to disarm** (arming stays free); a wrong PIN does nothing.

On the **tablet** put the Alarm Panel card pointed at the **PIN** entity; on the **phone** use the original one. The PIN is a local card lock, not the panel's own code.

## Roller shutters
The Smart shutter module runs the whole travel from a single pulse and has **no stop**: the motor relays are not driven directly (the module takes the relay back and the down travel doesn't work). It is driven through the panel's **outputs** instead. During the travel you can send another command and the shutter immediately takes the new one (e.g. from "open" to "close" without waiting for the end).

**Prerequisite on the panel (INIM/SmartLiving).** Before configuring the integration you must create, on the panel, some **outputs on virtual terminals** — one for each position you want to command — and assign each one the matching **shutter adjustment command**. You need **5 outputs per shutter**, one per command:

- **Open** (fully open)
- **Closed** (fully closed)
- **1/4** (25%)
- **1/2** (50%)
- **3/4** (75%)

It helps to give these outputs recognisable names (e.g. "Open living room blind", "Close living room blind", "Living room blind 1/2", …).

**Configuration in the integration.** Options → **Roller shutters** (the entry appears only if the panel has shutter modules). Each shutter's name is taken automatically from its Mod.dom module (it is the section title, not editable here). For each one choose the command output for every position: **Open** and **Closed** are required, **1/4 · 1/2 · 3/4** are optional and, if set, enable the position slider. The dropdown lists command outputs only: the raw motor terminals (`Mod.dom XX T03/T04`), used only to detect the shutters, cannot be selected. A shutter without the Open/Closed commands is not created.

## Show outputs not available via PrimeLAN
When off it shows only the outputs PrimeLAN publishes to the connected user; when on it also shows internal/mainboard outputs (siren, aux…).

## Hide unused areas
Hides the areas never named on the panel (with the default name like `AREA 013`) and their entities.

## Realtime push (optional, requires PrimeLAN)
It relies on the panel's **outbound HTTP API** ("Gestione Api Http in uscita"), part of the **PrimeLAN** Web API, so it is only available with PrimeLAN mode enabled. When on, the panel sends **events** to Home Assistant via a **local webhook**, so zones, partitions, outputs and faults update **instantly** without waiting for the poll (handy especially if you raise the scan intervals to ease the panel's load). The poll stays on as a reconciliation backstop.

How to enable it:
1. Options → *General parameters* → **Realtime push (local webhook)** → save. The webhook **local URL** appears **at the top** of General parameters (use HA's **local IP**: the panel is on the LAN).
2. In Prime/STUDIO → **PrimeLAN settings → Web API → outbound HTTP API**, create **one entry per event** (there is no wildcard), method **GET**, no authentication, pointing at the webhook URL plus parameters:

| Event | Parameters |
|---|---|
| Zone opened | `?ev=zone_open&id=<zone>` |
| Zone closed | `?ev=zone_close&id=<zone>` |
| Area armed | `?ev=arm&area=<area>` |
| Area disarmed | `?ev=disarm&area=<area>` |
| Alarm | `?ev=alarm&area=<area>` |
| Output | `?ev=output` |
| Tamper / fault | `?ev=tamper` / `?ev=fault` |

By default the webhook accepts **LAN** requests only (the panel calls it on the local IP). If HA is behind a **reverse proxy** and sees even local requests as «remote» (webhook rejected), enable **«Accept non-local requests»** in General parameters. The **secret id** in the URL is the key: keep it private. On each event the integration refreshes the affected entity right away.

> **Experimental**: the exact parameter names your panel sends should be verified in the field.

# Available devices
The integration groups the entities into a few devices. In Home Assistant their names all start with "Inim" (Inim Centrale, Inim Zone, Inim Aree, …), and so do the entity IDs.

## Inim Prime panel
Represents the panel.
- **Sensors:** Bypassed zones, Zones alarm memory, Partitions alarm memory, Active scenario.
- **Diagnostics:** Supply voltage, the **fault** sensors (Low battery, Mains fault, Phone line missing, Radio jamming, Tamper, GSM fault, Internet fault, etc.) and the **connection** sensors per integration mode (**Native connection** and/or **PrimeLAN connection**, `connectivity`): on when the mode's last poll succeeded, with the `ultimo_ok` attribute. Next to them, the **Native/PrimeLAN connection last update** sensors (`timestamp`) expose the same time as a separate entity (handy in cards/graphs) and stay "frozen" on the last good moment when the link drops. One of these appears for each configured mode too — two if you use both.
- **Event log** (event entity): tracks the panel logs. It is not real time (see the section on the log card).
- **Buttons:** Clear zone bypasses, Reset partition memories, and the **scenario** buttons.
- **Alarm** (`alarm_control_panel`): scenario-driven arming/disarming (if configured in the options).

## Zones
A single device holding all the zones. For each zone:
- **Binary sensor** (the zone's name) — open/closed state. Setting its type with `Show as` (Door, Window, Motion…) is recommended.
- **State** — one of FAULT / READY / ALARM / SHORT_CIRCUIT.
- **Bypass** (switch) — shows and changes the bypass state.
- **Alarm memory** (diagnostic).

## Partitions
A single device with all the partitions. For each one:
- **Arming state** (select) — DISARMED / INSTANT / PARTIAL / TOTAL.
- **State** — ALARM / READY / SABOTAGE.
- **Reset memory** (button).
- **Alarm memory** (diagnostic).

## Outputs
A single device with all the panel outputs, as switches (`switch`). **Dimmable** outputs become lights (`light`) with brightness **only in PrimeLAN mode**; in Native they are on/off.

## Roller shutters
A single device with the configured shutters (covers with Open/Close and optional position).

## Thermostats
A single device with the thermostats' temperature sensors (Native mode only, if present on the panel).

## Timers
A single device with the panel timers: an "active" binary sensor and an "enabled" switch for each. Created **only if the panel has timers** (like Thermostats, Roller shutters and Outputs).

## GSM
Operator, Signal strength, Credit, Supply voltage.

# Recommended configuration
## Rename the devices
Rename zones and partitions with meaningful names (they work by id, so renaming them is safe).
## Disable unused devices
You can disable the entities of zones/partitions you don't use.
## Pick the right type for the zone binary sensors
For each zone open the binary sensor, gear icon, and under `Show as` choose Door, Window, Motion, etc.

## Event log card (no custom cards)
The built-in **Markdown** card can show the log as a table by reading the `events` attribute of the event entity. Create a Markdown card and paste:
```yaml
type: markdown
title: Panel log
entity_id:
  - event.inim_prime_panel_log_events
content: >
  | Time | Event | Who | How |

  |---|---|---|---|

  {% for e in state_attr('event.inim_prime_panel_log_events', 'events') or [] -%}

  | {{ as_timestamp(e.timestamp) | timestamp_custom('%d/%m %H:%M:%S') }} | {{ e.type }} | {{ e.location or '' }} | {{ e.agent or '' }} |

  {% endfor %}
```
Replace `event.inim_prime_panel_log_events` with the id of your panel's event entity. The `events` attribute holds the latest events, each with `timestamp`, `type`, `location` and `agent`; adapt columns and date format as you like. For full width use a **Panel** type view.

### Log in a scrolling box (card-mod)
The log can get long and stretch the view. To wrap it in a **fixed-height box with internal scrolling** (fixed title, scrolling table) use [card-mod](https://github.com/thomasloven/lovelace-card-mod) (installable from HACS). Same Markdown card as above, with `grid_options` and `card_mod` added:
```yaml
type: markdown
title: Panel log
entity_id:
  - event.inim_prime_panel_log_events
content: >
  | Time | Event | Who | How |

  |---|---|---|---|

  {% for e in state_attr('event.inim_prime_panel_log_events', 'events') or [] -%}

  | {{ as_timestamp(e.timestamp) | timestamp_custom('%d/%m %H:%M:%S') }} | {{ e.type }} | {{ e.location or '' }} | {{ e.agent or '' }} |

  {% endfor %}
grid_options:
  columns: full
  rows: 8
card_mod:
  style: |
    ha-card {
      height: 100%;
      display: flex;
      flex-direction: column;
    }
    ha-card .card-content {
      overflow-y: auto;
    }
```
`rows` sets the box height (in a sections view, 1 row ≈ 56px: 8 rows ≈ a medium card); `overflow-y: auto` on the content scrolls the table without moving the rest. Increase or decrease `rows` to change the height.

# Inim Prime panel setup
## Requirements
- The **PrimeLAN** mode needs an Inim Prime panel with the **PrimeLAN network board**: without that board PrimeLAN cannot be used (you can buy and install it yourself or through an installer). The board manual is here: [Inim PrimeLAN manual](https://www.manualslib.com/manual/3494645/Inim-Primelan.html?page=4#manual).
- The **Native** mode needs the panel's local protocol port to be reachable (TCP, default **6004**) and the protocol password (default `pass`).

In both cases the panel is configured with the **Prime/STUDIO** software on Windows (download: [Inim Prime/STUDIO](https://archive.org/details/primestudio_4002_setup)).

## Prepare the panel
### Connecting to the panel
Open Prime/STUDIO and click `Open panel connected`, then select `Prime LAN`:

<img width="905" alt="img01" src="https://github.com/user-attachments/assets/9507ebab-88d9-46d6-84a3-f51cedd98854" />

Enter the panel's IP and password (default `pass`) and click `Next`.
Once communication is verified, you will see the detected panel, the firmware version and the **serial number**.
It is **STRONGLY RECOMMENDED** to copy the serial number correctly and keep it: you will need it later in the Home Assistant setup.
Then click `Open the panel`.

### Back up the configuration
The first thing to do, if you haven't already, is to copy the panel configuration.
Download the current configuration by clicking the `Download` icon at the top left:

<img width="634" height="113" alt="img02" src="https://github.com/user-attachments/assets/14a5bdee-61f4-4811-a8a0-f3a0932b18a2" />

When it finishes click `Save`, choose a name and `OK`.
If something goes wrong you can restore it by opening (`Open`) the saved configuration and pressing the `Upload` icon.

### Check the network settings
After the backup, click `Control panel parameters` in the left panel:

<img width="265" height="603" alt="img03" src="https://github.com/user-attachments/assets/6f3d5703-8dd9-416a-8562-cd8c72a93674" />

Then `IP connection parameters`:

<img width="693" height="83" alt="img04" src="https://github.com/user-attachments/assets/283bb9bc-3dec-4172-b275-bf16d8bb2944" />

Check whether `Obtain an IP address automatically` is enabled.
If it is, the panel uses parameters obtained from DHCP: Home Assistant needs a static IP (or at least a hostname). If you want to keep DHCP, reserve a static IP for the panel on your DHCP server (usually the router). If you prefer to disable DHCP, set the parameters and note down the IP address, which you will use in the integration setup.
After the changes, upload the updated configuration with `Upload`:

<img width="557" height="99" alt="img05" src="https://github.com/user-attachments/assets/cbf3c2b3-b2b0-44d0-8ddf-d0ca6fd3612c" />

### Update the firmware
Updating the panel firmware is recommended, by clicking here (top right):

<img width="112" height="102" alt="img06" src="https://github.com/user-attachments/assets/79168438-cfd7-42e8-93f4-d3dfaf3d19d9" />

### Configure PrimeLAN (PrimeLAN mode only)
With the panel updated, click `PrimeLAN settings` in the left panel:

<img width="298" height="603" alt="img07" src="https://github.com/user-attachments/assets/1d949b2c-cdca-49a8-b6c6-d9b8feb2ed98" />

Before working on it, load the current configuration with the download icon at the top left (it can take a while), then click `Web API` at the top right:

<img width="915" height="964" alt="img09" src="https://github.com/user-attachments/assets/c6742134-35d5-4fe2-b3bb-e82b51f3140d" />

Enable `API HTTP/HTTPS enable`, then `Generates` to create a new API Key.
You will need the API Key in the Home Assistant setup. It gives access to most of the panel's functions: if you think it has been compromised, regenerate it (remembering to upload the configuration afterwards).
Select which `Code` the Web API will use for its operations. A code dedicated to this purpose only is recommended.
Enabling `Only Allow connections via HTTPS` is recommended, to encrypt communications (the protocol is dated, but on a LAN it is fine).
The `Not allow disalarm` option is up to you: when enabled, some operations will fail, and at the moment the integration does not handle this case well.
When done, click `Upload` to load the new configuration.

# About the API library
The integration talks to the panel through a Python library created by Pitscheider, available here:
https://github.com/Pitscheider/inim_prime_api
You can use it to test your panel's API at a low level or to build something new.

# Trademarks
INIM and Inim Prime are trademarks or registered trademarks of INIM Electronics S.r.l.
This project is **not affiliated with, endorsed or supported by INIM Electronics S.r.l.**

# License
This project is released under the **GNU General Public License v3.0 (GPL-3.0)**.
You are free to use, modify and redistribute it under the terms of the GPL-3.0 license. See the `LICENSE` file for details.
