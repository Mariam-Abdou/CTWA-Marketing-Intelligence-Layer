"""
Full pipeline: Classifier -> Aggregator -> Score Corrector -> Action -> Allocation.
Runs on all campaigns, adsets, and ads, builds one scoreboard per level, and
hands the finished rows to src/reporting.py to write/print. Orchestration only
-- CSV/JSON writing and console printing live in reporting.py; the reasoning
text lives in src/reasoning/.
"""

from datetime import datetime

from src.ingestion.conversation_loader import load_conversations
from src.ingestion.meta_loader import (
    load_meta, insights_by_ad, insights_by_adset, insights_by_campaign, typical_campaign_days
)
from src.ingestion.joiner import join_conversations_to_meta
from src.scoring.aggregator import raw_rates
from src.scoring.corrector import compute_posterior, compute_top_level_posterior, estimate_prior_strength
from src.scoring.revenue import revenue_totals
from statistics import median

from src.auditing.audit import spend_totals, shrunk_roas_by_id, baseline_cpa as compute_baseline_cpa
from src.decision.outcome_decision import PROBABILITY_THRESHOLD, decide
from src.decision.action import resolve_action
from src.auditing.findings import flag_findings, print_findings
from src.decision.guardrails import describe_signals
from src.decision.allocation import build_allocation_plan
from src.decision.new_tests import propose_new_tests
from src.reasoning.llm import DecisionFacts
from src.reasoning.reasoning import narrate_all
from src.reasoning.hypothesis import build_hypotheses, rewrite_proposed
from src.reporting import write_csv, write_plan, print_scoreboard

_RUN = f"{datetime.now():%Y%m%d_%H%M%S}"
OUTPUT_FILE = f"outputs/scoreboard_{_RUN}.csv"
# Companion to the CSV. The CSV is the brief's deliverable (one row per id);
# this carries what has no id to sit on -- the budget split and the proposed
# tests -- so the app has one place to read a whole run from.
PLAN_FILE = f"outputs/plan_{_RUN}.json"

# exploit-scale first (highest confidence first), then explore, then none, then exploit-kill
_SORT_ORDER = {("exploit", "scale"): 0, ("explore", "hold"): 1, ("none", "hold"): 2, ("kill", "kill"): 3}


def _why(rc, posterior, insights=None) -> list[str]:
    """Merchant-readable evidence trail for this row -- also where a thin
    denominator gets flagged.

    The Meta fragment at the end is context, never cause: it is appended after
    the decision is already made, exactly as the brief's own example row pairs
    "3 sales / 18 clicks" with "CTR stable over 14 days"."""
    parts = [f"{rc.successes} success / {rc.n} resolved"]
    if rc.excluded:
        parts.append(f"{rc.excluded} excluded (in-flight/adversarial, outcome not known yet)")
    if posterior is not None:
        parts.append(f"90% interval [{posterior.interval_low:.3f}, {posterior.interval_high:.3f}]")
    signals = describe_signals(insights) if insights else None
    if signals:
        parts.append(signals)
    return parts


def build_labels(meta, level: str) -> dict[str, dict]:
    """id -> {name, detail}. `detail` surfaces exactly the thing the brief says
    each level IS -- campaign is the GOAL (objective), adset is the AUDIENCE
    (audience_type), ad is the CREATIVE (its theme/angle) -- so a scoreboard
    row reads as a strategy/audience/creative, not a 20-digit id."""
    if level == "campaign":
        return {c.id: {"name": c.name, "detail": f"goal={c.objective}"} for c in meta.campaigns}

    if level == "adset":
        return {a.id: {"name": a.name, "detail": f"audience={a.audience_type}"} for a in meta.adsets}

    if level == "ad":
        creatives_by_id = {c.id: c for c in meta.creatives}
        labels = {}
        for ad in meta.ads:
            creative = creatives_by_id.get(ad.creative_id) if ad.creative_id else None
            detail = f"creative={creative.theme}/{creative.angle}" if creative else "creative=n/a"
            labels[ad.id] = {"name": ad.name, "detail": detail}
        return labels

    raise ValueError(f"unknown level: {level}")


def build_scoreboard(rates, posteriors, get_baseline, plan, labels, insights_by_id=None):
    explore_by_id = {e.id: e for e in plan.explore}
    # budget_share only ever lives on exploit/explore entries; kill/none get 0.
    budget_share_by_id = {a.id: a.budget_share for a in plan.exploit}
    budget_share_by_id.update({e.id: e.budget_share for e in plan.explore})

    rows = []
    for id_, posterior in posteriors.items():
        rc = rates[id_]
        # bucket always comes from the plan -- including for posterior=None ids,
        # which the allocation plan may still be spending explore budget on.
        bucket = plan.bucket_by_id.get(id_, "none")
        e = explore_by_id.get(id_)
        label = labels.get(id_, {"name": id_, "detail": ""})

        baseline = get_baseline(id_)
        if posterior is None:
            action = "hold"
            interval = None
            score = None
        else:
            raw_action, p_better, p_worse = decide(posterior, baseline, rc.n)
            action = resolve_action(
                raw_action,
                is_fatigued=id_ in plan.fatigued,
                is_underperforming=id_ in plan.underperforming,
            )
            interval = (posterior.interval_low, posterior.interval_high)
            score = posterior.score

        rows.append({
            "id": id_, "name": label["name"], "detail": label["detail"],
            "raw_rate": rc.raw_rate, "score": score, "interval": interval,
            "successes": rc.successes, "n": rc.n, "excluded": rc.excluded, "baseline": baseline,
            "action": action, "bucket": bucket,
            "why": _why(rc, posterior, (insights_by_id or {}).get(id_)),
            "budget_share": budget_share_by_id.get(id_, 0.0),
            "hypothesis": e.hypothesis if e else None,
            "stop_rule": e.stop_rule if e else None,
        })

    rows.sort(key=lambda r: (_SORT_ORDER.get((r["bucket"], r["action"]), 4), -(r["score"] or 0)))
    return rows


def _median_roas(roas: dict[str, float]) -> float | None:
    """The money benchmark for this level, same shape as the rate baseline.
    Median, not mean: a couple of outliers at 5x drag a mean somewhere no
    campaign actually sits (see findings.md)."""
    return median(roas.values()) if roas else None


def _facts_for(r, level, plan, roas, confidence=None, roas_baseline=None):
    return DecisionFacts(
        level=level, id=r["id"], name=r["name"], detail=r["detail"],
        action=r["action"], bucket=r["bucket"],
        successes=r["successes"], n=r["n"], excluded=r["excluded"],
        raw_rate=r["raw_rate"], score=r["score"], interval=r["interval"],
        baseline=r["baseline"], roas=roas.get(r["id"]), roas_baseline=roas_baseline,
        budget_share=r["budget_share"],
        is_fatigued=r["id"] in plan.fatigued,
        is_underperforming=r["id"] in plan.underperforming,
        is_warned=r["id"] in plan.warned,
        confidence=confidence, stop_rule=r["stop_rule"],
    )


def attach_reasoning(rows, level, plan, roas):
    """Fills "reasoning" for every row, and rewrites "hypothesis" for the
    explore rows. Both run AFTER the decisions are final -- delete these two
    calls and the scoreboard is unchanged. See src/reasoning/llm.py."""
    roas_baseline = _median_roas(roas)
    texts = narrate_all([_facts_for(r, level, plan, roas, roas_baseline=roas_baseline) for r in rows])
    for r in rows:
        r["reasoning"] = texts[r["id"]]

    confidence_by_id = {e.id: max(e.p_better, e.p_worse) for e in plan.explore}
    explore_rows = [r for r in rows if r["bucket"] == "explore"]
    hypotheses = build_hypotheses([
        _facts_for(r, level, plan, roas, confidence_by_id.get(r["id"]), roas_baseline)
        for r in explore_rows
    ])
    for r in explore_rows:
        r["hypothesis"] = hypotheses[r["id"]]
    return rows

if __name__ == "__main__":
    convs = load_conversations("data/train/conv_train.json")
    meta = load_meta("data/train/meta_train.json")
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_rates, adset_rates, campaign_rates = raw_rates(joined)
    adset_to_campaign = {j.adset.id: j.campaign.id for j in joined}
    ad_to_adset = {j.ad.id: j.adset.id for j in joined}

    # Fit fresh from THIS run's data (all cycles so far) instead of a hardcoded
    # constant -- how much real rate-heterogeneity exists between adsets (resp.
    # ads) beyond what sampling noise alone explains sets how hard each level
    # gets pulled toward its parent. Re-run with more cycles of data and these
    # move on their own; nothing here needs to change by hand.
    adset_prior_strength = estimate_prior_strength(adset_rates)
    ad_prior_strength = estimate_prior_strength(ad_rates)
    print(f"Fitted prior strength: adset={adset_prior_strength:.2f}, ad={ad_prior_strength:.2f}\n")

    campaign_posteriors = {
        campaign_id: compute_top_level_posterior(campaign_rc)
        for campaign_id, campaign_rc in campaign_rates.items()
    }

    adset_posteriors = {}
    for adset_id, adset_rc in adset_rates.items():
        parent = campaign_posteriors[adset_to_campaign[adset_id]]
        baseline = parent.score if parent else 0.5
        adset_posteriors[adset_id] = compute_posterior(adset_rc, baseline, adset_prior_strength)

    ad_posteriors = {}
    for ad_id, ad_rc in ad_rates.items():
        parent = adset_posteriors[ad_to_adset[ad_id]]
        baseline = parent.score if parent else 0.5
        ad_posteriors[ad_id] = compute_posterior(ad_rc, baseline, ad_prior_strength)

    total_successes = sum(rc.successes for rc in campaign_rates.values())
    total_failures = sum(rc.failures for rc in campaign_rates.values())
    total_n = total_successes + total_failures
    overall_baseline = total_successes / total_n if total_n else 0.5

    campaign_baseline = lambda cid: overall_baseline
    adset_baseline = lambda aid: (
        campaign_posteriors[adset_to_campaign[aid]].score
        if campaign_posteriors[adset_to_campaign[aid]] else 0.5
    )
    ad_baseline = lambda aid: (
        adset_posteriors[ad_to_adset[aid]].score
        if adset_posteriors[ad_to_adset[aid]] else 0.5
    )

    ad_revenue, adset_revenue, campaign_revenue = revenue_totals(joined)
    ad_spend, adset_spend, campaign_spend = spend_totals(meta)
    campaign_roas = shrunk_roas_by_id(campaign_revenue, campaign_spend)
    adset_roas = shrunk_roas_by_id(adset_revenue, adset_spend)
    ad_roas = shrunk_roas_by_id(ad_revenue, ad_spend)

    campaign_cpa_inputs = {id_: (campaign_spend.get(id_), rev.n) for id_, rev in campaign_revenue.items()}
    adset_cpa_inputs = {id_: (adset_spend.get(id_), rev.n) for id_, rev in adset_revenue.items()}
    ad_cpa_inputs = {id_: (ad_spend.get(id_), rev.n) for id_, rev in ad_revenue.items()}

    campaign_baseline_cpa = compute_baseline_cpa(campaign_revenue, campaign_spend)
    adset_baseline_cpa = compute_baseline_cpa(adset_revenue, adset_spend)
    ad_baseline_cpa = compute_baseline_cpa(ad_revenue, ad_spend)

    horizon_days = typical_campaign_days(meta)
    # Ranked once; each level takes only the slots its live entities left empty.
    # Adset level only. A proposal is an audience x creative pairing, so it is one
    # test -- offering the same pairing at campaign and ad level too would fund it
    # three times over. Campaign level is a choice of objective, which this does
    # not generate.
    new_test_candidates = propose_new_tests(joined, meta, overall_baseline, horizon_days)
    print(f"Test horizon: {horizon_days:.0f} days (median campaign length)\n")

    campaign_plan = build_allocation_plan(
        campaign_posteriors, campaign_baseline, rates=campaign_rates,
        daily_insights_by_ad=insights_by_campaign(meta),
        shrunk_roas=campaign_roas,
        spend_and_orders=campaign_cpa_inputs, baseline_cpa=campaign_baseline_cpa,
        horizon_days=horizon_days,
    )
    adset_plan = build_allocation_plan(
        adset_posteriors, adset_baseline, rates=adset_rates,
        daily_insights_by_ad=insights_by_adset(meta),
        shrunk_roas=adset_roas,
        spend_and_orders=adset_cpa_inputs, baseline_cpa=adset_baseline_cpa,
        horizon_days=horizon_days, proposals=new_test_candidates,
    )
    ad_plan = build_allocation_plan(
        ad_posteriors, ad_baseline, rates=ad_rates,
        daily_insights_by_ad=insights_by_ad(meta),
        shrunk_roas=ad_roas,
        spend_and_orders=ad_cpa_inputs, baseline_cpa=ad_baseline_cpa,
        horizon_days=horizon_days,
    )

    campaign_rows = build_scoreboard(
        campaign_rates, campaign_posteriors, campaign_baseline, campaign_plan, build_labels(meta, "campaign"),
        insights_by_campaign(meta),
    )
    adset_rows = build_scoreboard(
        adset_rates, adset_posteriors, adset_baseline, adset_plan, build_labels(meta, "adset"),
        insights_by_adset(meta),
    )
    ad_rows = build_scoreboard(
        ad_rates, ad_posteriors, ad_baseline, ad_plan, build_labels(meta, "ad"),
        insights_by_ad(meta),
    )

    attach_reasoning(campaign_rows, "campaign", campaign_plan, campaign_roas)
    attach_reasoning(adset_rows, "adset", adset_plan, adset_roas)
    attach_reasoning(ad_rows, "ad", ad_plan, ad_roas)

    # Proposed (never-run) tests have no scoreboard row and no id in `rows`,
    # so attach_reasoning() above never touches them -- rewrite in place here.
    if adset_plan.proposed:
        proposed_hyps = rewrite_proposed(adset_plan.proposed, overall_baseline)
        for t in adset_plan.proposed:
            t.hypothesis = proposed_hyps[t.id]

    print(f"\nProbability threshold: {PROBABILITY_THRESHOLD:.2f}\n")
    print_scoreboard("Campaigns", campaign_rows)
    print()
    print_scoreboard("Adsets", adset_rows)
    print()
    print_scoreboard("Ads", ad_rows)

    for label, plan in (("Campaign", campaign_plan), ("Adset", adset_plan), ("Ad", ad_plan)):
        if not plan.proposed:
            continue
        # Deliberately not scoreboard rows: these have no id to key on, and the
        # brief's output contract is one row per existing id.
        print(f"\n--- {label}: proposed tests (no data yet, not in the scoreboard) ---")
        for t in plan.proposed:
            print(f"  {t.name}  [{t.budget_share:.0%} of budget]")
            print(f"    hypothesis: {t.hypothesis}")
            print(f"    stop_rule:  {t.stop_rule}")

    for label, rows_, roas_ in (("Campaigns", campaign_rows, campaign_roas),
                                ("Adsets", adset_rows, adset_roas),
                                ("Ads", ad_rows, ad_roas)):
        print()
        print_findings(label, flag_findings(rows_, roas_))

    write_csv(OUTPUT_FILE, [("campaign", campaign_rows), ("adset", adset_rows), ("ad", ad_rows)])
    campaign_names = {c.id: c.name for c in meta.campaigns}
    adset_names = {a.id: a.name for a in meta.adsets}
    write_plan(
        PLAN_FILE,
        [("campaign", campaign_rows, campaign_plan,
          (campaign_spend, campaign_revenue, campaign_roas), {}),
         ("adset", adset_rows, adset_plan,
          (adset_spend, adset_revenue, adset_roas),
          {a: campaign_names.get(c) for a, c in adset_to_campaign.items()}),
         ("ad", ad_rows, ad_plan,
          (ad_spend, ad_revenue, ad_roas),
          {a: adset_names.get(s_) for a, s_ in ad_to_adset.items()})],
        horizon_days,
        {"adset": adset_prior_strength, "ad": ad_prior_strength},
    )
    print(f"\nWrote {OUTPUT_FILE} and {PLAN_FILE}")
