# Changelog

## 1.1.1

- Keep zero-hashrate restart samples inside historical incidents until mining actually resumes.
- Reconcile previously derived v1.1.0 incidents without changing raw samples or legacy events.

## 1.1.0

- Persistent flight-recorder incidents with five-minute pre-crash statistics.
- Correlated mining-stall, power-interruption, restart, thermal, pool and API-outage diagnoses.
- ONLINE, DEGRADED, OFFLINE and RECOVERING states with configurable confirmation windows.
- Input current calculated from measured power and input voltage; raw AxeOS current remains diagnostic only.
- Improved reject information, block-candidate wording, health summary and incident timeline.
- Additive SQLite migration that preserves all existing samples and events.
