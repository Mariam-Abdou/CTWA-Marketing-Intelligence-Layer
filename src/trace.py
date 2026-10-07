"""
Collects what the pipeline already computed for every id and hands it to the
trace database (src/storage/trace_db.py). Called once, at the end of
src/pipeline.py, after every decision is final.

Rule this file keeps: it does not decide anything. The only things it
computes are bookkeeping (which conversation belongs to which id, how each
conversation was classified) using the SAME functions the scoring used.
"""

from collections import Counter
from datetime import datetime

from .config import load_config
from .ingestion.io_utils import load_json
from .scoring.classifier import MIN_SUCCESS_AMOUNT, classify
from .scoring.amounts import get_outcome_amounts
from .auditing.findings import flag_findings
from .decision.outcome_decision import PROBABILITY_THRESHOLD, MIN_N_FOR_ACTION
from .storage.trace_db import build_steps, write_run
from .insights.signals import conversation_signals
from .insights.money import order_money
from .insights.customers import build_customers

_cfg = load_config()


def _classify_conversations(convs, join_result, raw_by_id=None):
    joined_by_id = {j.conversation.id: j for j in join_result.scoreable}
    organic = {c.id for c in join_result.organic_or_direct}
    out = {}
    for c in convs:
        net = get_outcome_amounts(c).net
        success = classify(c).success
        j = joined_by_id.get(c.id)
        if j is not None:
            join_status = "scored: linked to ad, adset and campaign"
        elif c.id in organic:
            join_status = "not scored: organic/direct, no ad behind it"
        else:
            join_status = "not scored: ad id not found in meta data"
        out[c.id] = {
            "outcome_type": c.outcome.type,
            "net_amount": net,
            "success": success,
            "classification": ("success" if success else "failure") if success is not None
                              else "excluded: outcome not known yet",
            "join_status": join_status,
            "ad_id": j.ad.id if j else c.source.ad_id,
            "adset_id": j.adset.id if j else None,
            "campaign_id": j.campaign.id if j else c.source.campaign_id,
        }
        if raw_by_id and c.id in raw_by_id:
            out[c.id]["signals"] = conversation_signals(raw_by_id[c.id], success)
    return out


def _entities(meta, scored_ids):
    creatives = {c.id: c for c in meta.creatives}
    out = []
    for c in meta.campaigns:
        out.append({"level": "campaign", "id": c.id, "name": c.name, "detail": f"goal={c.objective}",
                    "campaign_id": c.id, "adset_id": None, "parent_id": None,
                    "scored": c.id in scored_ids})
    for a in meta.adsets:
        out.append({"level": "adset", "id": a.id, "name": a.name, "detail": f"audience={a.audience_type}",
                    "campaign_id": a.campaign_id, "adset_id": a.id, "parent_id": a.campaign_id,
                    "scored": a.id in scored_ids})
    for ad in meta.ads:
        cr = creatives.get(ad.creative_id) if ad.creative_id else None
        out.append({"level": "ad", "id": ad.id, "name": ad.name,
                    "detail": f"creative={cr.theme}/{cr.angle}" if cr else "creative=n/a",
                    "campaign_id": ad.campaign_id, "adset_id": ad.adset_id, "parent_id": ad.adset_id,
                    "scored": ad.id in scored_ids})
    return out


def record_trace(*, run_id, paths, convs, meta, join_result, overall_baseline, overall_baseline_inputs,
                 horizon_days,
                 prior_details, levels, adset_to_campaign, ad_to_adset,
                 campaign_posteriors, adset_posteriors):
    db_cfg = _cfg.get("trace_db", {})
    db_path = db_cfg.get("path", "outputs/trace.db")

    raw_conversations = load_json(paths["conversations"])
    conv_class = _classify_conversations(convs, join_result, {r["id"]: r for r in raw_conversations})

    # which conversations count toward which id -- same grouping as aggregator.raw_rates
    links = {}
    for j in join_result.scoreable:
        for eid in (j.ad.id, j.adset.id, j.campaign.id):
            links.setdefault(eid, []).append(j.conversation.id)

    scored_ids = set()
    for lv in levels.values():
        scored_ids.update(lv["rates"].keys())
    entities = _entities(meta, scored_ids)

    level_out = {}
    for level, lv in levels.items():
        rows_by_id = {r["id"]: r for r in lv["rows"]}
        findings = {}
        for f in flag_findings(lv["rows"], lv["roas"]):
            findings.setdefault(f["id"], []).append(f)
        money_context = {
            "baseline_cpa": lv["baseline_cpa"], "median_roas": lv["median_roas"],
            "probability_threshold": PROBABILITY_THRESHOLD, "min_n_for_action": MIN_N_FOR_ACTION,
        }
        steps = {}
        for eid, rate in lv["rates"].items():
            row = rows_by_id[eid]
            if level == "campaign":
                prior_info = {"baseline_source": "account-wide success rate over all resolved conversations"}
            elif level == "adset":
                parent = adset_to_campaign[eid]
                pp = campaign_posteriors.get(parent)
                prior_info = {"parent_level": "campaign", "parent_id": parent,
                              "parent_score": pp.score if pp else 0.5,
                              "prior_strength_details": prior_details["adset"],
                              "baseline_source": "parent campaign's score" if pp else "0.5 (parent has no score)"}
            else:
                parent = ad_to_adset[eid]
                pp = adset_posteriors.get(parent)
                prior_info = {"parent_level": "adset", "parent_id": parent,
                              "parent_score": pp.score if pp else 0.5,
                              "prior_strength_details": prior_details["ad"],
                              "baseline_source": "parent adset's score" if pp else "0.5 (parent has no score)"}
            conv_counter = Counter(conv_class[c]["outcome_type"] for c in links.get(eid, []))
            steps[eid] = build_steps(
                level=level, entity_id=eid, row=row, rate=rate,
                posterior=lv["posteriors"].get(eid), prior_info=prior_info,
                plan_trace=lv["plan"].trace.get(eid),
                roas_detail={**(lv["roas_details"].get(eid) or {}), **order_money([
                    {"outcome_type": conv_class[c]["outcome_type"],
                     **(conv_class[c].get("signals") or {})} for c in links.get(eid, [])])},
                money_context=money_context, findings=findings.get(eid, []),
                conv_counter=conv_counter, min_success_amount=MIN_SUCCESS_AMOUNT,
            )
        level_out[level] = {"rows_by_id": rows_by_id, "steps": steps,
                            "series": lv["series"], "findings": findings}

    # ids in meta with no conversations: say so instead of leaving them blank
    for e in entities:
        if not e["scored"]:
            level_out[e["level"]]["steps"][e["id"]] = [{
                "step_no": 1, "step_key": "conversations", "title": "Count and classify this id's conversations",
                "applied": False, "changed_outcome": False,
                "rule": "No conversations in this data point to this id, so it has no score and no decision.",
                "inputs": {"conversations_linked": 0}, "outputs": {},
            }]

    proposed = []
    for level, lv in levels.items():
        for t in lv["plan"].proposed:
            proposed.append({
                "level": level, "id": t.id, "name": t.name, "audience_type": t.audience_type,
                "theme": t.theme, "budget_share": t.budget_share, "expected_rate": t.expected_rate,
                "hypothesis": t.hypothesis, "hypothesis_source": getattr(t, "hypothesis_source", None),
                "hypothesis_rule_based": getattr(t, "hypothesis_rule_based", None),
                "stop_rule": t.stop_rule, "audience_rate": t.audience_rate, "audience_n": t.audience_n,
                "theme_rate": t.theme_rate, "theme_n": t.theme_n, "baseline": t.baseline,
            })

    run_params = {
        "probability_threshold": PROBABILITY_THRESHOLD,
        "min_n_for_action": MIN_N_FOR_ACTION,
        "min_success_amount": MIN_SUCCESS_AMOUNT,
        "overall_baseline": overall_baseline,
        "overall_baseline_inputs": overall_baseline_inputs,
        "horizon_days": horizon_days,
        "prior_strength_fit": prior_details,
        "exploit_share": _cfg["allocation"]["exploit_share"],
        "explore_share": _cfg["allocation"]["explore_share"],
        "max_explore_tests": _cfg["allocation"]["max_explore_tests"],
        "per_level": {lvl: {"baseline_cost_per_sale": lv["baseline_cpa"],
                            "baseline_cost_per_sale_inputs": lv.get("baseline_cpa_inputs"),
                            "median_roas": lv["median_roas"]}
                      for lvl, lv in levels.items()},
        "join_counts": {"scored": len(join_result.scoreable),
                        "organic_or_direct": len(join_result.organic_or_direct),
                        "unmatched_ctwa": len(join_result.unmatched_ctwa)},
    }

    raw_by_id = {r["id"]: r for r in raw_conversations}
    customers = build_customers([{
        "customer_id": (raw_by_id[cid].get("customer") or {}).get("id"), "cycle": raw_by_id[cid].get("cycle"),
        "started_at": raw_by_id[cid].get("started_at"),
        "success": None if c["success"] is None else int(c["success"]),
        "platform": (raw_by_id[cid].get("source") or {}).get("platform"), "ad_id": c["ad_id"],
        "city": (c.get("signals") or {}).get("city"),
        "ordered_products": (c.get("signals") or {}).get("ordered_products"),
        "order_value": (c.get("signals") or {}).get("gross_amount"),
        "refunds": (c.get("signals") or {}).get("refunded_amount"), "net": c["net_amount"],
    } for cid, c in conv_class.items() if cid in raw_by_id],
        {p["id"]: p.get("name", p["id"]) for p in load_json(paths["products"])})

    write_run(
        db_path, run_id=run_id, generated_at=datetime.now().isoformat(timespec="seconds"),
        paths=paths, config=_cfg, run_params=run_params,
        raw_conversations=raw_conversations,
        raw_meta=load_json(paths["meta"]), raw_products=load_json(paths["products"]),
        conv_class=conv_class, conv_links=links, entities=entities,
        levels=level_out, proposed=proposed, keep_runs=db_cfg.get("keep_runs", 10),
        customers=customers,
    )
    return db_path
