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


def is_fatigued(daily_insights: list[DailyInsight]) -> bool:
    if len(daily_insights) < FATIGUE_MIN_DAYS_FOR_CTR_CHECK:
        return False

    avg_frequency = _recent_avg_frequency(daily_insights)
    if avg_frequency is None or avg_frequency < FATIGUE_FREQUENCY_THRESHOLD:
        return False

    first_window_ctrs = [i.ctr for i in daily_insights[:FATIGUE_WINDOW_DAYS] if i.ctr is not None]
    last_window_ctrs = [i.ctr for i in daily_insights[-FATIGUE_WINDOW_DAYS:] if i.ctr is not None]
    if not first_window_ctrs or not last_window_ctrs:
        return False

    first_avg = _avg(first_window_ctrs)
    last_avg = _avg(last_window_ctrs)
    if first_avg <= 0:
        return False

    ctr_drop = (first_avg - last_avg) / first_avg
    return ctr_drop >= FATIGUE_CTR_DROP_THRESHOLD


def frequency_warning(daily_insights: list[DailyInsight]) -> bool:
    if len(daily_insights) < FATIGUE_WINDOW_DAYS:
        return False

    avg_frequency = _recent_avg_frequency(daily_insights)
    if avg_frequency is None:
        return False

    return FATIGUE_WARNING_FREQUENCY_THRESHOLD <= avg_frequency < FATIGUE_FREQUENCY_THRESHOLD


def is_underperforming(spend: float | None, orders: int, baseline_cpa: float | None) -> bool:
    if spend is None or baseline_cpa is None:
        return False
    if spend < baseline_cpa * MIN_SPEND_MULTIPLIER:
        return False
    if orders == 0:
        return True
    return (spend / orders) > baseline_cpa * CPA_STOP_MULTIPLIER


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
