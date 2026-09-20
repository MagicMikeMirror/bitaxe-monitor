# Bitaxe Monitor

A lightweight, privacy-first dashboard for Bitaxe and AxeOS miners. It polls the
local AxeOS API, stores long-term telemetry in SQLite and detects operational
events without Grafana, InfluxDB or additional containers.

## Features

- 10-second polling with SQLite persistence
- Current health and mining telemetry
- 1-hour, 24-hour and 7-day charts
- Correlated offline, recovery, restart, power, thermal, pool and mining-stall incidents
- Informational rejected-share events and cautious block-candidate detection
- Live BTC/EUR plus the decoded AxeOS miner coinbase value and transaction fees
- Compact 24-hour BTC/EUR chart with change, low and high
- Public Pool statistics for the configured miner and its workers
- Mining-stall incidents with duration, stages and strictly observed causes
- Persistent flight recorder with five-minute pre-incident snapshots and min/max/average values
- Correlated incident classification instead of repeated low-hashrate alarms
- Robust ONLINE / DEGRADED / OFFLINE / RECOVERING state machine
- Input current calculated from power and input voltage (`I = P / U`)
- Diagnostic capture of safe AxeOS power and hardware fault fields
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
| `AUTO_RESTART_THRESHOLD_PCT` | `70` | Percentage of expected hashrate that starts the timer |
| `AUTO_RESTART_AFTER_SECONDS` | `600` | Continuous degradation required before restart |
| `AUTO_RESTART_MIN_UPTIME_SECONDS` | `900` | Never restart during the initial warm-up period |
| `AUTO_RESTART_COOLDOWN_SECONDS` | `21600` | Minimum six-hour interval between attempts |
| `AUTO_RESTART_VERIFY_SECONDS` | `900` | Time allowed for recovery after the attempt |

The hashrate card shows the live AxeOS value plus 10-minute and 1-hour AxeOS
averages. Its 24-hour and 7-day values are calculated from the persistent
SQLite history as time-weighted production averages. Confirmed downtime counts
as zero; isolated missed polls do not become artificial outages. Until a full
window is available, the dashboard labels the actual data coverage.

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
for the full delay. Every attempt and its result are persisted as an incident. A
six-hour cooldown prevents restart loops.

The original AxeOS `current` value is retained in the allow-listed diagnostic
payload, but the displayed input current is calculated from measured watts and
input voltage. ESP-Miner v2.15.1 documents `current` as milliamps and AxeOS divides
it by 1000; calculating `P/U` makes the user-facing value internally consistent.
See the official [ESP-Miner v2.15.1 API schema](https://github.com/bitaxeorg/ESP-Miner/blob/v2.15.1/main/http_server/openapi.yaml)
and [AxeOS display mapping](https://github.com/bitaxeorg/ESP-Miner/blob/v2.15.1/main/http_server/axe-os/src/app/components/home/home.component.ts).

## Updating

Back up `/DATA/AppData/bitaxe-monitor/data`, change the image tag to `1.1.0`, and
recreate the container. Startup only adds new SQLite tables; existing samples and
events are not rewritten or deleted.

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
