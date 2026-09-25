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
