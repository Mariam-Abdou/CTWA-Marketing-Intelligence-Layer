"""
Demo run: classifier -> aggregator -> score_corrector, for one real campaign->adset->ad chain.
"""

from src.ingestion.conversation_loader import load_conversations
from src.ingestion.meta_loader import load_meta
from src.ingestion.joiner import join_conversations_to_meta
from src.scoring.classifier import classify
from src.scoring.aggregator import raw_rates
from src.scoring.corrector import compute_posterior, PRIOR_STRENGTH

convs = load_conversations("data/train.json")
meta = load_meta("data/meta_data.json")
joined = join_conversations_to_meta(convs, meta).scoreable

ad_rates, adset_rates, campaign_rates = raw_rates(joined)

sample_ad_id = "120209876543210009"  # n=5, raw_rate=0.8 -- small-but-not-tiny sample
jc_sample = next(j for j in joined if j.ad.id == sample_ad_id)
adset_id = jc_sample.adset.id
campaign_id = jc_sample.campaign.id

ad_conversations = [jc.conversation for jc in joined if jc.ad.id == sample_ad_id]

print("=" * 60)
print("STEP 1 CLASSIFIER (per conversation, for this ad)")
print("=" * 60)
for c in ad_conversations:
    r = classify(c)
    print(f"  {c.id} | outcome={c.outcome.type:12s} | value={r.value} | success={r.success}")

print()
print("=" * 60)
print("STEP 2 AGGREGATOR (raw counts, independent per level)")
print("=" * 60)
campaign_rc = campaign_rates[campaign_id]
adset_rc = adset_rates[adset_id]
ad_rc = ad_rates[sample_ad_id]

print(f"Campaign {campaign_id}: {campaign_rc}")
print(f"   Adset {adset_id}: {adset_rc}")
print(f"      Ad {sample_ad_id}: {ad_rc}")

print()
print("=" * 60)
print(f"STEP 3 SCORE CORRECTOR (Beta-Binomial shrinkage, prior_strength={PRIOR_STRENGTH})")
print("=" * 60)
adset_posterior = compute_posterior(adset_rc, campaign_rc.raw_rate, PRIOR_STRENGTH)
ad_posterior = compute_posterior(ad_rc, adset_posterior.score, PRIOR_STRENGTH)

print(f"Campaign {campaign_id}: raw_rate={campaign_rc.raw_rate:.4f} (n={campaign_rc.n}) [top level, no shrinkage]")
print(f"   Adset {adset_id}: raw_rate={adset_rc.raw_rate:.4f} -> shrunk={adset_posterior.score:.4f}, "
      f"90% interval=[{adset_posterior.interval_low:.4f}, {adset_posterior.interval_high:.4f}] (n={adset_rc.n})")
print(f"      Ad {sample_ad_id}: raw_rate={ad_rc.raw_rate:.4f} -> shrunk={ad_posterior.score:.4f}, "
      f"90% interval=[{ad_posterior.interval_low:.4f}, {ad_posterior.interval_high:.4f}] (n={ad_rc.n})")