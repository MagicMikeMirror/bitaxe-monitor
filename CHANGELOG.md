# Changelog

## 1.3.7

- Fix the historical user-pause confirmation endpoint startup dependency.

## 1.3.6

- Allow a specifically user-confirmed legacy incident to be retained for audit but reclassified as a planned USER_PAUSED interval.
- Represent the corrected interval through USER_PAUSED / USER_RESUMED events and exclude it from the technical incident list and statistics.

## 1.3.5

- Track deliberate AxeOS mining pauses as persistent USER_PAUSED / USER_RESUMED events rather than technical incidents.
- Suppress stall detection and auto-recovery during pauses and the shared resume stabilization period, including a final pre-restart pause guard.
- Keep pause telemetry unchanged while excluding planned pause time from technical hashrate averages.
- Mark planned pause spans in historical charts and distinguish offline periods following a user pause.

## 1.3.4

- Add profile-button tooltips with frequency, core voltage, fan mode and custom-OC status sourced from the live profile API.

## 1.3.3

- Disable automatic fan control and set a fixed 100% fan speed in every mining profile.

## 1.3.2

- Allow an existing named custom dashboard layout to be updated in place while keeping the standard layout immutable.

## 1.3.1

- Apply mining profiles immediately without restarting AxeOS.
- Remove hidden cards from the dashboard grid and render minimized cards as compact, reopenable rows.

## 1.3.0

- Show current mining efficiency in J/TH and neutral 24-hour input-voltage min/average/max values.
- Preserve complete allow-listed AxeOS telemetry immediately before automatic recovery.
- Track the neutral BM1370 Error Count delta from the last stable pre-incident sample without treating counter resets as negative errors.
- Add synchronized incident charts from five minutes before detection through five minutes after recovery, including raw domain values and event markers.
- Add pre-incident 60-second and five-minute voltage minima without assigning undocumented voltage severity thresholds.
- Add an immutable standard dashboard layout plus named persistent custom layouts.
- Allow cards to be reordered, minimized and hidden in an explicit edit mode; every saved variation requires a new unique name.
- Add guarded Gamma 601 mining profiles that atomically pair frequency/voltage with automatic fan control and a temperature target.
- Verify the complete profile from subsequent AxeOS telemetry; custom OC profiles require an explicit warning confirmation.

## 1.2.4

- Detect at least two zero-hash ASIC domains independently of aggregate hashrate spikes.
- Confirm the fault over three polls and restart after 60 seconds with all existing safety gates.
- Persist domain indexes and values in the forensic incident snapshot and require all four domains for recovery.
- Allow one second domain-stall recovery inside the normal cooldown while retaining the two-per-hour hard limit.

## 1.2.3

- Use compact coverage labels in the hashrate card so 1h, 24h and 7d values never overlap.

## 1.2.2

- Calculate 1h, 24h and 7d hashrate averages exclusively from persistent SQLite telemetry.
- Display exact coverage percentages for every historical average.
- Record every observed AxeOS uptime reset as a restart marker without splitting history.
- Add an end-to-end regression test proving that two boot sessions remain in charts, averages and storage.

## 1.2.1

- Show unavailable legacy pre-restart voltage as an em dash instead of `NaN`.

## 1.2.0

- Use true timestamp axes for 1h, 24h and 7d charts, preserving telemetry gaps and adding incident markers.
- Persist a fresh allow-listed diagnostic snapshot and exact AxeOS domain/register values before every automatic restart.
- Define the configured 70% threshold as hashrate loss against a stable baseline and retain baseline, minimum, average and duration evidence.
- Limit automatic recovery to two restart attempts per rolling 60 minutes and expose suppression as an event.
- Add incident detail views with pre/post telemetry and a strict observed/derived distinction.
- Keep wallet names, pool credentials, network identifiers and raw API responses out of storage and UI.

## 1.1.14

- Add an accessible on/off switch for automatic recovery to the device-status card.
- Persist dashboard changes in SQLite and show the active state immediately.

## 1.1.13

- Re-arm automatic recovery 30 minutes after a successful restart.
- Make one second restart attempt when the first does not recover within 15 minutes.
- Lock only after two consecutive failed attempts and automatically unlock after stable recovery.

## 1.1.12

- Persist the auto-restart opt-in in SQLite for appliances that cannot add environment variables.
- Expose the active state in the current-status API and health summary.

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
# 1.4.0 — 2026-09-25

- Preserve the old device history while waiting for a replacement; require explicit identity assignment before ingest or recovery.
- Isolate generations in separate SQLite files with HMAC identities, verified backups and restart-safe switch operations.
- Add boot/counter epochs, unique sample IDs and deterministic connection cleanup.
- Add domain/error/heap diagnostics, a separate voltage plot, windowed efficiency and share quality, and a unified timeline.
- Record configuration changes; distinguish regulator faults from external power interruptions and observed restarts from recovery requests.
- Keep Bitcoin price and block value expanded; refresh market data without a connected miner.
- Preserve existing raw samples and layouts. No destructive reset or automatic retention deletion.

## 1.4.1

- Use a pure black background and neutral glass cards, controls and dialogs. Styling only; behavior and stored data unchanged.
