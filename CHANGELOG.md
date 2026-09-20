# Changelog

## 1.1.11

- Add opt-in automatic AxeOS recovery for sustained partial hashrate degradation.
- Require normal power/frequency, minimum uptime and no reported fault before acting.
- Persist the trigger, restart request and recovery result as one incident.
- Enforce a six-hour cooldown and report unsuccessful recovery without restart loops.

## 1.1.10

- Use AxeOS decoded coinbase values for the current miner block value.
- Show transaction fees separately from the halving-based block subsidy.
- Fall back to a clearly labelled subsidy-only value when coinbase data is unavailable.

## 1.1.7

- Show the last successful dashboard refresh time beside ONLINE.
- Add a per-second countdown to the next automatic refresh.

## 1.1.6

- Add a compact 24-hour BTC/EUR price chart to the Bitcoin card.
- Show 24-hour percentage change and daily low/high with green/red direction.

## 1.1.5

- Consolidate Health Summary and device details into one compact status card.
- Promote the current BTC/EUR spot price in the Bitcoin and block-value card.
- Show Coinbase as the price source together with the last refresh time.

## 1.1.4

- Label ASIC temperature, VR temperature, power and input voltage explicitly.
- Render VR temperature as a dashed yellow line so the red ASIC line remains visible when values overlap.

## 1.1.3

- Give incident and event types responsive space and prevent labels from overlapping descriptions.
- Improve timeline wrapping on desktop and mobile widths.

## 1.1.2

- Display seconds for sub-hour incidents so short restarts never appear as zero-duration events.

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
