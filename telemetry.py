"""Reset-aware, time-weighted diagnostics from allow-listed samples."""
import math
import statistics


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def configuration(data):
    return {key: data.get(key) for key in ('frequency', 'coreVoltage', 'autofanspeed', 'manualFanSpeed', 'temptarget')}


def continuous(before, after, interval, max_gap=30):
    if not before or not after or not 0 < interval <= max_gap:
        return False
    if any(s.get('miningPaused') or s.get('overheat_mode') or s.get('power_fault') or s.get('hardware_fault') for s in (before, after)):
        return False
    if configuration(before) != configuration(after):
        return False
    for key in ('generation', 'bootSession', 'counterEpoch'):
        if before.get(key) and after.get(key) and before[key] != after[key]:
            return False
    a, b = number(before.get('uptimeSeconds')), number(after.get('uptimeSeconds'))
    if a is None or b is None or b < a or abs((b - a) - interval) > max(5, interval * .5):
        return False
    if any((number(s.get('hashRate')) or 0) <= 0 for s in (before, after)):
        return False
    return True


def counter_delta(before, after):
    a, b = number(before), number(after)
    # A decrease is ambiguous (ASIC reset or wrap); do not invent a large modular delta.
    return b - a if a is not None and b is not None and b >= a else None


def domains(data):
    asics = (data.get('hashrateMonitor') or {}).get('asics') or []
    return [number(v) for v in (asics[0].get('domains') or [])] if asics else []


# Monitor thresholds, not vendor specifications. On the deployed Gamma 601 the
# observed healthy baseline was -3.2% (5m) and -1.35% (15m) for the weakest
# domain. These deliberately wider bands avoid reacting to normal BM1370 jitter.
DOMAIN_WATCH_5M_PCT = -12.0
DOMAIN_WATCH_15M_PCT = -8.0
DOMAIN_WARNING_15M_PCT = -15.0
DOMAIN_MIN_COVERAGE = 0.75


def _domain_window(samples, end, seconds, max_gap):
    start = end - seconds
    totals, covered = [0.0] * 4, 0.0
    for before, after in zip(samples, samples[1:]):
        interval = after['ts'] - before['ts']
        duration = min(end, after['ts']) - max(start, before['ts'])
        values = domains(before)
        if (duration <= 0 or not continuous(before, after, interval, max_gap)
                or len(values) != 4 or any(value is None or value < 0 for value in values)):
            continue
        for index, value in enumerate(values):
            totals[index] += value * duration
        covered += duration
    if covered <= 0:
        return {'seconds': seconds, 'coverage_seconds': 0, 'coverage_pct': 0,
                'averages': None, 'deviations_pct': None,
                'weakest_domain': None, 'weakest_deviation_pct': None,
                'max_downward_pct': None}
    averages = [total / covered for total in totals]
    common = statistics.mean(averages)
    deviations = [100 * (value - common) / common for value in averages] if common > 0 else None
    weakest = min(range(4), key=lambda index: deviations[index]) if deviations else None
    return {'seconds': seconds, 'coverage_seconds': covered,
            'coverage_pct': min(100.0, covered * 100 / seconds),
            'averages': averages, 'deviations_pct': deviations,
            'weakest_domain': weakest,
            'weakest_deviation_pct': deviations[weakest] if weakest is not None else None,
            'max_downward_pct': -deviations[weakest] if weakest is not None else None}


def domain_stability(samples, end, max_gap=30):
    """Time-based four-domain assessment; a single raw sample never sets status."""
    windows = {label: _domain_window(samples, end, seconds, max_gap)
               for label, seconds in (('5m', 300), ('15m', 900))}
    five, fifteen = windows['5m'], windows['15m']
    required_coverage = 900 * DOMAIN_MIN_COVERAGE
    if (not fifteen['averages'] or fifteen['coverage_seconds'] < required_coverage
            or len(fifteen['averages']) != 4):
        status, reason = 'UNKNOWN', 'Unvollständige 15-Minuten-Daten'
        active = stable = None
    else:
        active = sum(value > 1 for value in fifteen['averages'])
        deviations = fifteen['deviations_pct']
        stable = (sum(value > 1 and deviation > DOMAIN_WATCH_15M_PCT
                      for value, deviation in zip(fifteen['averages'], deviations))
                  if deviations is not None else 0)
        weakest_15 = fifteen['weakest_deviation_pct']
        weakest_5 = five['weakest_deviation_pct']
        five_minute_dead = (five['coverage_seconds'] >= 300 * DOMAIN_MIN_COVERAGE
                            and any(value <= 1 for value in five['averages']))
        if active < 4 or five_minute_dead:
            status, reason = 'WARNING', 'Mindestens eine Domain liefert dauerhaft kaum oder keine Hashrate'
        elif weakest_15 <= DOMAIN_WARNING_15M_PCT:
            status, reason = 'WARNING', 'Eine Domain liegt über 15 Minuten deutlich zurück'
        elif weakest_15 <= DOMAIN_WATCH_15M_PCT:
            status, reason = 'WATCH', 'Beginnende persistente Unterperformance im 15-Minuten-Mittel'
        elif (five['coverage_seconds'] >= 300 * DOMAIN_MIN_COVERAGE
              and weakest_5 <= DOMAIN_WATCH_5M_PCT):
            status, reason = 'WATCH', 'Kurzfristige Unterperformance im 5-Minuten-Mittel'
        else:
            status, reason = 'PASS', 'Alle vier Domains im Zeitmittel stabil'
    latest = samples[-1] if samples else {}
    actual, expected = number(latest.get('hashRate')), number(latest.get('expectedHashrate'))
    return {'status': status, 'reason': reason, 'windows': windows,
            'active_domains': active, 'stable_domains': stable, 'domain_count': 4,
            'actual_expected_pct': (actual * 100 / expected if actual is not None and expected and expected > 0 else None),
            'asic_error_pct': number(latest.get('errorPercentage')),
            'thresholds': {'watch_5m_pct': DOMAIN_WATCH_5M_PCT,
                           'watch_15m_pct': DOMAIN_WATCH_15M_PCT,
                           'warning_15m_pct': DOMAIN_WARNING_15M_PCT,
                           'minimum_coverage_pct': DOMAIN_MIN_COVERAGE * 100}}


def errors(data):
    asics = (data.get('hashrateMonitor') or {}).get('asics') or []
    return number(asics[0].get('errorCount')) if asics else None


def summarize(samples, end, seconds, max_gap=30, low_voltage=4750):
    start = end - seconds
    samples = [s for s in samples if start - max_gap <= s['ts'] <= end]
    error_delta = accepted = rejected = jobs = 0
    counter_seconds = covered = energy = hashes = low_seconds = active_seconds = paused_seconds = 0.0
    error_weight = error_time = 0.0
    invalid_counter = False
    for before, after in zip(samples, samples[1:]):
        interval = after['ts'] - before['ts']
        duration = min(end, after['ts']) - max(start, before['ts'])
        if duration <= 0 or not 0 < interval <= max_gap:
            continue
        covered += duration
        if before.get('miningPaused'):
            paused_seconds += duration
        elif (number(before.get('hashRate')) or 0) > 0:
            active_seconds += duration
        voltage = number(before.get('voltage'))
        if voltage is not None and voltage < low_voltage:
            low_seconds += duration
        if continuous(before, after, interval, max_gap):
            ratio = duration / interval
            error = counter_delta(errors(before), errors(after))
            a = counter_delta(before.get('sharesAccepted'), after.get('sharesAccepted'))
            r = counter_delta(before.get('sharesRejected'), after.get('sharesRejected'))
            j = counter_delta(before.get('workReceived'), after.get('workReceived'))
            if error is not None:
                error_delta += error * ratio
                counter_seconds += duration
            else:
                invalid_counter = True
            if a is not None and r is not None:
                accepted += a * ratio
                rejected += r * ratio
            if j is not None:
                jobs += j * ratio
            power, rate = number(before.get('power')), number(before.get('hashRate'))
            if power is not None and power >= 0 and rate is not None and rate > 0:
                energy += power * duration
                hashes += rate / 1000 * duration
        else:
            invalid_counter = True
        pct = number(before.get('errorPercentage'))
        if pct is not None and not before.get('miningPaused') and (number(before.get('hashRate')) or 0) > 0:
            error_weight += pct * duration
            error_time += duration
    values = [s for s in samples if s['ts'] >= start]
    def stats(key):
        nums = [v for s in values if (v := number(s.get(key))) is not None]
        return {'min': min(nums), 'max': max(nums), 'avg': statistics.mean(nums)} if nums else None
    latest = values[-1] if values else {}
    ds = domains(latest)
    valid = [v for v in ds if v is not None]
    total_shares = accepted + rejected
    return {'seconds': seconds, 'coverage_seconds': covered,
            'counter_seconds': counter_seconds, 'counter_interrupted': invalid_counter,
            'error_delta': error_delta if counter_seconds else None,
            'error_per_minute': error_delta * 60 / counter_seconds if counter_seconds else None,
            'error_pct': error_weight / error_time if error_time else None,
            'reject_pct': rejected * 100 / total_shares if total_shares else None,
            'accepted_delta': accepted, 'rejected_delta': rejected, 'jobs_delta': jobs,
            'efficiency_jth': energy / hashes if hashes else None,
            'energy_wh': energy / 3600, 'work_th': hashes,
            'voltage_low_seconds': low_seconds, 'mining_seconds': active_seconds,
            'pause_seconds': paused_seconds, 'voltage': stats('voltage'),
            'temp': stats('temp'), 'vrTemp': stats('vrTemp'),
            'freeHeapInternal': stats('freeHeapInternal'), 'maxAllocHeap': stats('maxAllocHeap'),
            'domains': ds, 'active_domains': sum(v > 1 for v in valid),
            'known_domains': len(valid), 'domain_count': len(ds),
            'domain_imbalance_pct': (max(valid) - min(valid)) * 100 / max(valid) if valid and max(valid) > 0 else None}
