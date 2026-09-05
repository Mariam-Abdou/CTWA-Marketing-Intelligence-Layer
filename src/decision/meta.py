from ..ingestion.meta_loader import DailyInsight

# Grounded thresholds (see reasoning log): frequency >= 3.0 = fatigue zone
# (cold/prospecting audiences), 2.5-3.0 = warning only, no veto. CTR drop
# compares an ad's own first vs most recent FATIGUE_WINDOW_DAYS.
FATIGUE_FREQUENCY_THRESHOLD = 3.0
FATIGUE_WARNING_FREQUENCY_THRESHOLD = 2.5
FATIGUE_CTR_DROP_THRESHOLD = 0.25
FATIGUE_WINDOW_DAYS = 7
FATIGUE_MIN_DAYS_FOR_CTR_CHECK = FATIGUE_WINDOW_DAYS * 2


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
