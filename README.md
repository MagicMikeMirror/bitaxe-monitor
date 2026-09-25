# Bitaxe Monitor

## Device generations (1.4.0)

The existing database stays assigned to **Gamma 1 – existing history** on upgrade.
No replacement device is created, and no history is reset. Until an operator
confirms the identity of a reachable device, polling cannot append samples to the
old history or trigger automatic recovery. This is intentional when the old miner
has already been returned and the replacement has not arrived.

Use **Gerätewechsel vorbereiten** after the replacement is connected. The dialog
offers a new history with the old one archived, or an explicit assignment to the
old device. A fresh API read must provide a valid MAC; IP/hostname are never used
as identity. The normalized MAC is stored only as a keyed HMAC in a local catalog.
The raw MAC is not stored or exposed. Archive selection is read-only.

Each generation owns a separate SQLite database. This deliberately isolates all
existing queries, incidents, pauses, cooldowns and recovery state. The catalog
`generations.sqlite3` selects the active database; the original `bitaxe.sqlite3`
also retains shared dashboard layouts. New generations start with auto-restart
disabled. Boot sessions and counter epochs distinguish restarts, ASIC counter
resets, pauses and collection gaps within a generation. The catalog change uses
a durable operation record; an interrupted switch is finished before polling.

Before migration or switching, SQLite's backup API creates a consistent snapshot
including committed WAL data, verifies integrity and table counts, and records a
SHA-256 checksum. Switch backups also include the catalog and identity key.
Keep the entire data directory, including `.device-identity.key`, in private
backups; never commit or publish it. A missing key fails closed. Backups and
archives are retained; no automatic deletion or destructive reset is provided.

For recovery, stop the monitor first. Restore a coherent data-directory backup,
including the catalog, referenced generation files and identity key. A switch
backup's `telemetry.sqlite3` is a snapshot of the former active database: restore
it under the filename referenced by the backed-up catalog, alongside all other
referenced archive files. Never mix a catalog from one backup with another key.
Migration-only backups can restore the pre-upgrade original database with the
previous application image. Test restoration on an isolated copy first.

The dashboard now includes four domain traces, ASIC-error windows (1/5/15 min),
reset-aware ErrorCount deltas, windowed reject rates and efficiency, a separate
input-voltage axis, heap diagnostics, configuration comparisons and a filtered
incident/event timeline. Bitcoin's price chart and block value remain expanded.
The market price refresh also works while the miner is absent. Pool/worker
statistics are external history and do not reset with a local generation.

Input current derived as power/input voltage is an estimate. With TPS546,
the API current is regulator output current and power includes a board offset.
ASIC error percentage is distinct from pool reject percentage. Negative sensor
sentinels and invalid domain positions remain unknown rather than becoming zero.
No ErrorCount delta is inferred across a reset, pause, ambiguous wrap or gap.

`/api/system/asic` is read on hardware/firmware change; heap and additional sensor
fields come from the existing info poll. Optional raw logs, WebSockets and
scoreboard import remain disabled: they are not required for these diagnostics.
The diagnostic plots are a 15-minute flight-recorder view. Persistent 1h/24h/7d
production charts remain available. `/healthz` reports monitor/database readiness;
miner availability is reported separately, so an absent replacement does not mark
the healthy monitor container as broken.

For a source build, run `docker compose build` before recreating the service.
Locally built images do not imply that a corresponding public registry release
has been published.

A lightweight, privacy-first dashboard for Bitaxe and AxeOS miners. It polls the
local AxeOS API, stores long-term telemetry in SQLite and detects operational
events without Grafana, InfluxDB or additional containers.

## Features

- 10-second polling with SQLite persistence
- Current health and mining telemetry
- 1-hour, 24-hour and 7-day charts
- Correlated offline, recovery, restart, power, thermal, pool and mining-stall incidents
- Restart-proof 1h, 24h and 7d averages calculated from persistent SQLite telemetry
- Informational rejected-share events and cautious block-candidate detection
- Live BTC/EUR plus the decoded AxeOS miner coinbase value and transaction fees
- Compact 24-hour BTC/EUR chart with change, low and high
- Public Pool statistics for the configured miner and its workers
- Mining-stall incidents with duration, stages and strictly observed causes
- Persistent flight recorder with five-minute pre-incident snapshots and min/max/average values
- Correlated incident classification instead of repeated low-hashrate alarms
- Robust ONLINE / DEGRADED / OFFLINE / RECOVERING state machine
- Persistent USER_PAUSED / USER_RESUMED tracking with a restart-safe resume grace period
- Planned-pause chart spans and performance averages that exclude deliberate pause time without altering raw telemetry
- Input current calculated from power and input voltage (`I = P / U`)
- Current mining efficiency calculated from observed power and hashrate (`W / TH/s = J/TH`)
- Neutral 24-hour input-voltage minimum, average and maximum from persistent telemetry
- Diagnostic capture of safe AxeOS power and hardware fault fields
- Clickable incident forensics with synchronized telemetry from five minutes before detection through five minutes after recovery
- BM1370 Error Count and reset-safe delta from the last stable pre-incident sample
- Immutable standard dashboard layout with drag-and-drop, minimize/hide controls and named SQLite-backed custom layouts that can be updated or copied
- Gamma 601 mining profiles: Eco (490/1100), Standard (525/1150), OC (650/1180) and Performance (725/1220), with value tooltips, automatic fan control disabled and fan speed fixed at 100%, applied without restarting AxeOS
- Responsive dark dashboard for TV, desktop and mobile
- Single multi-architecture container with no Python dependencies

## Privacy

The AxeOS response is filtered through an explicit allow-list before it reaches
SQLite, logs or the dashboard. The app never stores raw API responses, wallet
addresses, pool URLs, pool users, pool passwords, Wi-Fi SSIDs, MAC addresses or
device IP addresses.

For the optional Public Pool statistics, the configured mining address is read
from AxeOS and used only in memory for the pool request. It is never returned by
the dashboard API, written to SQLite or included in logs. BTC/EUR is refreshed
from Coinbase. The miner block value uses AxeOS's decoded coinbase satoshi value;
the subsidy follows the current block height and is the clearly labelled fallback
when decoded coinbase data is unavailable.

## ZimaOS installation

1. Download `docker-compose.yml` from this repository.
2. Set `BITAXE_API_URL` to your miner's `/api/system/info` endpoint.
3. Install the compose file as a custom ZimaOS app.
4. Open `http://<zimaos-ip>:8787`.

Persistent data is stored at
`/DATA/AppData/bitaxe-monitor/data/bitaxe.sqlite3`.

## Docker Compose

Copy `.env.example` to `.env`, adjust the AxeOS URL and run:

```sh
docker compose up -d
```

Health endpoint: `http://localhost:8787/healthz`

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `BITAXE_API_URL` | `http://192.168.1.100/api/system/info` | AxeOS API endpoint |
| `POLL_SECONDS` | `10` | Poll interval, minimum 5 seconds |
| `DASHBOARD_PORT` | `8787` | Published dashboard port |
| `POWER_HIGH_W` | `35` | High-power event threshold |
| `TEMP_HIGH_C` | `75` | High-temperature event threshold |
| `HASHRATE_LOW_GH` | `750` | Low-hashrate event threshold |
| `PUBLIC_POOL_API_URL` | `https://public-pool.io:40557/api` | Public Pool API endpoint |
| `BTC_PRICE_URL` | Coinbase BTC/EUR spot API | BTC/EUR price endpoint |
| `BTC_HISTORY_URL` | Coinbase Exchange BTC/EUR candles | Public hourly candles for the 24-hour chart |
| `MARKET_SECONDS` | `300` | Market and Public Pool refresh interval |
| `OFFLINE_AFTER_POLLS` | `3` | Failed polls required before OFFLINE |
| `RECOVERY_POLLS` | `3` | Successful polls required before ONLINE |
| `STALL_AFTER_POLLS` | `3` | Consecutive stopped-mining polls before an incident |
| `IDLE_POWER_W` | `8` | Upper controller-idle power used for stall correlation |
| `VOLTAGE_LOW_V` | `4.75` | Low-input-voltage diagnostic threshold |
| `EXPECTED_HASHRATE_GH` | `0` | Optional expected hashrate; 0 uses AxeOS when available |
| `AUTO_RESTART_ENABLED` | `false` | Restart AxeOS after sustained partial hashrate loss |
| `AUTO_RESTART_THRESHOLD_PCT` | `70` | Hashrate loss in percent that starts the timer (70 means restart below 30% of the stable baseline) |
| `AUTO_RESTART_AFTER_SECONDS` | `600` | Continuous degradation required before restart |
| `AUTO_RESTART_MIN_UPTIME_SECONDS` | `900` | Never restart during the initial warm-up period |
| `AUTO_RESTART_COOLDOWN_SECONDS` | `1800` | Re-arm delay after a successful recovery |
| `AUTO_RESTART_VERIFY_SECONDS` | `900` | Time allowed for recovery after the attempt |
| `DOMAIN_STALL_POLLS` | `3` | Consecutive polls with at least two stalled ASIC domains before confirming the fault |
| `DOMAIN_STALL_AFTER_SECONDS` | `60` | Persistent domain-stall duration before guarded restart |

The hashrate card shows the live AxeOS value plus its 10-minute average.
Its 1-hour, 24-hour and 7-day values are calculated from the persistent
SQLite history as time-weighted production averages. Confirmed downtime counts
as zero; isolated missed polls do not become artificial outages. Until a full
window is available, the dashboard labels the actual data coverage.

## Dashboard layouts

The built-in standard layout is immutable. Select **Anpassen** to reorder,
minimize or hide cards. A named custom layout can be updated directly with
**Änderungen speichern** or copied with **Als neues Layout speichern**. Layouts
are stored in the persistent SQLite database.

## Mining profiles

The four Gamma 601 / BM1370 profiles apply frequency, core voltage, disabled
automatic fan control and a fixed 100% fan speed together in one AxeOS request. Profile
changes take effect without an AxeOS restart and are confirmed from subsequent
telemetry. OC and Performance use custom values and therefore require an
explicit warning confirmation in the dashboard.

## Flight recorder and diagnosis

Version 1.1 stores incidents separately from the unchanged raw sample history. Each
incident contains its start/end time, observed facts, recovery, last good sample,
and min/max/average telemetry for the preceding five minutes. A stale AxeOS
`resetReason` is never treated as a new cause: it is correlated only when the
monitored uptime actually resets. Direct AxeOS `power_fault` signals take priority.

The classification is an evidence-based diagnostic aid, not an electrical
measurement instrument. Transient faults can occur between polls; uncertain cases
remain `UNKNOWN` instead of being presented as facts.

Automatic restart is disabled by default. When enabled, it only acts while AxeOS is
reachable, power and frequency indicate active mining, no fault/overheat/pause or
fallback-pool state is present, and hashrate remains below the configured percentage
for the full delay. Every attempt and its result are persisted as one incident.
After a successful recovery the guard is armed again after 30 minutes. If the first
restart does not recover within 15 minutes, one second attempt is made. Two failed
attempts lock further automatic restarts until three stable measurements are observed,
for example after a manual restart. This prevents loops without limiting successful
future recoveries.

The setting can also be stored persistently in SQLite without changing container
environment variables: `POST /api/settings/auto-restart` with JSON
`{"enabled":true}`. The current state is returned by `/api/current` and included
in the health summary. A switch in the health and device-status card changes the
same setting directly from the dashboard.

An AxeOS pause (`miningPaused=true`) is stored as `USER_PAUSED`, not as a
technical incident. Hashrate and domain-stall detection as well as automatic
recovery remain suppressed throughout the pause and a short resume grace period.
If the device becomes unreachable after a deliberate pause, the monitor records
the observed offline/online sequence without assigning an unproven failure cause.
Zero-hashrate samples remain intact in SQLite and visible in charts; deliberate
pause time is reported separately and excluded from technical performance averages.

The original AxeOS `current` value is retained in the allow-listed diagnostic
payload, but the displayed input current is calculated from measured watts and
input voltage. ESP-Miner v2.15.1 documents `current` as milliamps and AxeOS divides
it by 1000; calculating `P/U` makes the user-facing value internally consistent.
See the official [ESP-Miner v2.15.1 API schema](https://github.com/bitaxeorg/ESP-Miner/blob/v2.15.1/main/http_server/openapi.yaml)
and [AxeOS display mapping](https://github.com/bitaxeorg/ESP-Miner/blob/v2.15.1/main/http_server/axe-os/src/app/components/home/home.component.ts).

## Updating

Back up `/DATA/AppData/bitaxe-monitor/data`, build version `1.4.0` from source or
use its published image when available, and recreate the container. The sample
table gains a separate row ID so duplicate timestamps cannot overwrite telemetry.
Existing sample timestamps/payloads, incidents and layouts are preserved. Startup
does not bind a new device or create its history.

## Supported AxeOS versions

The monitor is tested against AxeOS / ESP-Miner v2.15.1. Other recent versions
using `/api/system/info` may work, but field availability can differ.

## Development

The application uses only Python's standard library:

```sh
python -m unittest discover tests
python app.py
```

Licensed under the MIT License.
