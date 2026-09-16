# Bitaxe Monitor

A lightweight, privacy-first dashboard for Bitaxe and AxeOS miners. It polls the
local AxeOS API, stores long-term telemetry in SQLite and detects operational
events without Grafana, InfluxDB or additional containers.

## Features

- 10-second polling with SQLite persistence
- Current health and mining telemetry
- 1-hour, 24-hour and 7-day charts
- Offline, recovery, reboot, power, temperature, low-hashrate, rejected-share,
  fallback-pool, overheat and block-found events
- Responsive dark dashboard for TV, desktop and mobile
- Single multi-architecture container with no Python dependencies

## Privacy

The AxeOS response is filtered through an explicit allow-list before it reaches
SQLite, logs or the dashboard. The app never stores raw API responses, wallet
addresses, pool URLs, pool users, pool passwords, Wi-Fi SSIDs, MAC addresses or
device IP addresses.

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

## Development

The application uses only Python's standard library:

```sh
python -m unittest discover tests
python app.py
```

Licensed under the MIT License.
