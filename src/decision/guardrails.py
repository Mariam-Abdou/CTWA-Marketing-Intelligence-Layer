from dataclasses import dataclass
from ..ingestion.meta_loader import DailyInsight
from ..config import load_config

_cfg = load_config()
_fatigue_cfg = _cfg["fatigue"]
_cpa_cfg = _cfg["cpa"]

FATIGUE_FREQUENCY_THRESHOLD = _fatigue_cfg["frequency_threshold"]
FATIGUE_WARNING_FREQUENCY_THRESHOLD = _fatigue_cfg["warning_frequency_threshold"]
FATIGUE_CTR_DROP_THRESHOLD = _fatigue_cfg["ctr_drop_threshold"]
FATIGUE_WINDOW_DAYS = _fatigue_cfg["window_days"]
FATIGUE_MIN_DAYS_FOR_CTR_CHECK = FATIGUE_WINDOW_DAYS * 2

# don't judge CPA until spend >= min_spend_multiplier x baseline CPA, 
# flag if CPA > stop_multiplier x baseline
MIN_SPEND_MULTIPLIER = _cpa_cfg["min_spend_multiplier"]
CPA_STOP_MULTIPLIER = _cpa_cfg["stop_multiplier"]


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _recent_avg_frequency(
    daily_insights: list[DailyInsight], window_days: int = FATIGUE_WINDOW_DAYS
) -> float | None:
    recent = daily_insights[-window_days:]
    freqs = [i.frequency for i in recent if i.frequency is not None]
    return _avg(freqs)


def fatigue_check(daily_insights: list[DailyInsight]) -> dict:
    """The fatigue veto and the frequency warning, with every number they read.
    is_fatigued() and frequency_warning() return fields of this dict, so the
    trace stored for an id is exactly the working the veto acted on."""
    days = len(daily_insights)
    avg_frequency = _recent_avg_frequency(daily_insights) if daily_insights else None

    first_avg = last_avg = ctr_drop = None
    if days >= FATIGUE_MIN_DAYS_FOR_CTR_CHECK:
        first = [i.ctr for i in daily_insights[:FATIGUE_WINDOW_DAYS] if i.ctr is not None]
        last = [i.ctr for i in daily_insights[-FATIGUE_WINDOW_DAYS:] if i.ctr is not None]
        if first and last:
            first_avg, last_avg = _avg(first), _avg(last)
            if first_avg > 0:
                ctr_drop = (first_avg - last_avg) / first_avg

    if days < FATIGUE_MIN_DAYS_FOR_CTR_CHECK:
        fatigued, reason = False, f"only {days} days of delivery; needs {FATIGUE_MIN_DAYS_FOR_CTR_CHECK} to compare first vs last week"
    elif avg_frequency is None or avg_frequency < FATIGUE_FREQUENCY_THRESHOLD:
        fatigued, reason = False, "recent frequency below the fatigue threshold"
    elif first_avg is None or last_avg is None:
        fatigued, reason = False, "no CTR data in the first or last window"
    elif first_avg <= 0:
        fatigued, reason = False, "first-week CTR is zero, no drop to measure"
    else:
        fatigued = ctr_drop >= FATIGUE_CTR_DROP_THRESHOLD
        reason = ("frequency at/above threshold AND CTR dropped at least the threshold vs first week"
                  if fatigued else "frequency at/above threshold but CTR drop below threshold")

    if days < FATIGUE_WINDOW_DAYS or avg_frequency is None:
        warning = False
    else:
        warning = FATIGUE_WARNING_FREQUENCY_THRESHOLD <= avg_frequency < FATIGUE_FREQUENCY_THRESHOLD

    return {
        "days": days,
        "window_days": FATIGUE_WINDOW_DAYS,
        "min_days_for_ctr_check": FATIGUE_MIN_DAYS_FOR_CTR_CHECK,
        "recent_avg_frequency": avg_frequency,
        "frequency_threshold": FATIGUE_FREQUENCY_THRESHOLD,
        "warning_frequency_threshold": FATIGUE_WARNING_FREQUENCY_THRESHOLD,
        "first_window_avg_ctr": first_avg,
        "last_window_avg_ctr": last_avg,
        "ctr_drop": ctr_drop,
        "ctr_drop_threshold": FATIGUE_CTR_DROP_THRESHOLD,
        "fatigued": fatigued,
        "frequency_warning": warning,
        "reason": reason,
    }


def is_fatigued(daily_insights: list[DailyInsight]) -> bool:
    return fatigue_check(daily_insights)["fatigued"]


def frequency_warning(daily_insights: list[DailyInsight]) -> bool:
    return fatigue_check(daily_insights)["frequency_warning"]


def cpa_check(spend: float | None, orders: int, baseline_cpa: float | None) -> dict:
    """The CPA veto with every number it read. is_underperforming() returns
    its "underperforming" field."""
    judge_line = baseline_cpa * MIN_SPEND_MULTIPLIER if baseline_cpa is not None else None
    stop_line = baseline_cpa * CPA_STOP_MULTIPLIER if baseline_cpa is not None else None
    cpa = spend / orders if spend is not None and orders else None

    if spend is None or baseline_cpa is None:
        under, reason = False, "no spend or no baseline CPA to compare against"
    elif spend < judge_line:
        under, reason = False, "spend has not reached the judging line yet"
    elif orders == 0:
        under, reason = True, "spent past the judging line with zero orders"
    else:
        under = cpa > stop_line
        reason = "CPA above the stop line" if under else "CPA under the stop line"

    return {
        "spend": spend, "orders": orders, "cpa": cpa, "baseline_cpa": baseline_cpa,
        "min_spend_multiplier": MIN_SPEND_MULTIPLIER, "judge_line": judge_line,
        "stop_multiplier": CPA_STOP_MULTIPLIER, "stop_line": stop_line,
        "underperforming": under, "reason": reason,
    }


def is_underperforming(spend: float | None, orders: int, baseline_cpa: float | None) -> bool:
    return cpa_check(spend, orders, baseline_cpa)["underperforming"]


if __name__ == "__main__":
    from ..ingestion.meta_loader import load_meta, insights_by_ad

    meta = load_meta("data/train/meta_train.json")
    by_ad = insights_by_ad(meta)

    for ad_id in sorted(by_ad.keys()):
        insights = by_ad[ad_id]
        avg_frequency = _recent_avg_frequency(insights)
        freq_str = f"{avg_frequency:.2f}" if avg_frequency is not None else "n/a"
        print(
            f"{ad_id}: days={len(insights):<3} avg_frequency={freq_str:<6} "
            f"fatigued={is_fatigued(insights)} warning={frequency_warning(insights)}"
        )


@dataclass
class MetaSignals:
    days: int
    avg_frequency: float | None
    ctr_change: float | None  # negative = CTR fell against the ad's own first week


def meta_signals(daily_insights: list[DailyInsight]) -> MetaSignals:
    days = len(daily_insights)
    avg_frequency = _recent_avg_frequency(daily_insights)

    ctr_change = None
    if days >= FATIGUE_MIN_DAYS_FOR_CTR_CHECK:
        first = [i.ctr for i in daily_insights[:FATIGUE_WINDOW_DAYS] if i.ctr is not None]
        last = [i.ctr for i in daily_insights[-FATIGUE_WINDOW_DAYS:] if i.ctr is not None]
        if first and last:
            first_avg, last_avg = _avg(first), _avg(last)
            if first_avg:
                ctr_change = (last_avg - first_avg) / first_avg

    return MetaSignals(days=days, avg_frequency=avg_frequency, ctr_change=ctr_change)


def describe_signals(daily_insights: list[DailyInsight]) -> str | None:
    """One short fragment for the evidence trail, in the brief's own style."""
    signals = meta_signals(daily_insights)
    if signals.days < FATIGUE_MIN_DAYS_FOR_CTR_CHECK:
        return f"only {signals.days} days of delivery, too short to read a trend" if signals.days else None

    parts = []
    if signals.avg_frequency is not None:
        parts.append(f"frequency {signals.avg_frequency:.1f}")
    if signals.ctr_change is not None:
        if abs(signals.ctr_change) < 0.10:
            parts.append(f"CTR stable over {signals.days} days")
        else:
            direction = "down" if signals.ctr_change < 0 else "up"
            parts.append(f"CTR {direction} {abs(signals.ctr_change):.0%} vs first week")
    return ", ".join(parts) or None
