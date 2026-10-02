"""
Every term the system uses, in merchant language, with the numbers read
from config.yaml so the glossary can never drift from what the pipeline
actually does. Served to the answer step through the glossary() tool.
"""

import re
from difflib import get_close_matches

from ..config import load_config

_c = load_config()
_d, _f, _cpa, _a, _s = _c["decision"], _c["fatigue"], _c["cpa"], _c["allocation"], _c["scoring"]

TERMS = {
    "sale": ("success",
             "A conversation counts as a sale (success) when its net amount -- order total minus any refund -- "
             "is at least the price of the cheapest product. Ghosted, cancelled and fully refunded chats are "
             "failures. Chats still open, stuck pending, or adversarial are excluded: their outcome is not known yet."),
    "score": ("conversion score, rate",
              "The estimated chance that a conversation from this campaign/audience/ad ends in a sale. It blends "
              "the item's own results with what is typical for its parent (the 'prior'), so a handful of chats "
              "cannot swing it to an extreme. It is a probability between 0 and 1."),
    "interval": ("90% interval, credible interval, range",
                 "The range the true sale rate very likely sits in (90% probability). Narrow = lots of evidence; "
                 "wide = few conversations, so the score is uncertain."),
    "baseline": ("benchmark, bar",
                 "What the item is compared against. A campaign is compared with the whole account's sale rate; "
                 "an audience with its campaign's score; an ad with its audience's score."),
    "prior": ("prior strength, shrinkage",
              "Before looking at an item's own chats, the system assumes it performs like its parent. Prior "
              "strength says how many 'virtual conversations' that assumption is worth; it is fitted from the "
              "data each run (more real differences between items -> weaker prior)."),
    "p_better": ("p_worse, probability better, confidence",
                 "P(better) is the probability the item's true sale rate is above its baseline; P(worse) is the "
                 "probability it is below."),
    "scale": ("exploit",
              f"Scale = proven better than its baseline: P(better) >= {_d['probability_threshold']} with at least "
              f"{_d['min_n_for_action']} resolved conversations, and no guardrail objected. Scaled items share the "
              f"exploit budget ({_a['exploit_share']:.0%})."),
    "kill": ("stop",
             f"Kill = proven worse than its baseline: P(worse) >= {_d['probability_threshold']} with at least "
             f"{_d['min_n_for_action']} resolved conversations. It gets no budget."),
    "hold": ("undecided",
             "Hold = not enough evidence to call it better or worse, or a guardrail stopped a scale. A held item "
             "may still get a test budget (explore) or nothing."),
    "70/30": ("budget split, exploit explore split, allocation",
              f"The next budget is split {_a['exploit_share']:.0%} exploit (scale what is proven, weighted by "
              f"P(better) x return on spend) and {_a['explore_share']:.0%} explore (at most "
              f"{_a['max_explore_tests']} tests, equal shares)."),
    "explore": ("test, test slot, explore test",
                f"An explore test is an item that is not proven either way but worth a small budget to find out. "
                f"Only items that can be judged within one typical campaign length qualify; they are ranked by "
                f"how good they could plausibly be (top of the interval x return on spend) and the top "
                f"{_a['max_explore_tests']} get the slots. Others 'lost the test slot'."),
    "proposed test": ("new test, never run",
                      "An audience x creative pairing never run before, suggested when live items leave a test "
                      "slot free. Its expected rate is a naive guess, which is why it is a test and not a decision."),
    "fatigue": ("audience fatigue, frequency",
                f"Fatigue guardrail: only checked when the numbers say scale. If the same people saw the ad on "
                f"average >= {_f['frequency_threshold']} times recently AND click-through dropped >= "
                f"{_f['ctr_drop_threshold']:.0%} versus its first {_f['window_days']} days, scale is changed to "
                f"hold. A frequency of {_f['warning_frequency_threshold']}-{_f['frequency_threshold']} is only a "
                f"warning."),
    "cost per sale": ("cpa, cost per acquisition, cpa guardrail, baseline cost per sale",
                      f"Spend divided by real sales. Baseline cost per sale = all spend at that level / all sales. "
                      f"Guardrail: only checked when the numbers say scale; once spend passes "
                      f"{_cpa['min_spend_multiplier']}x the baseline cost per sale, scale becomes hold if there are "
                      f"zero sales or the cost per sale is above {_cpa['stop_multiplier']}x the baseline."),
    "roas": ("return on ad spend, shrunk roas",
             "Revenue (net of refunds) divided by Meta spend. The system uses a 'shrunk' ROAS that is pulled toward "
             "the account average when there are few conversations. ROAS is used for budget weighting and "
             "guardrails, never inside the score."),
    "stop rule": ("stop criterion, when to stop",
                  "For a test: how much more spend (and roughly how many days at its recent pace) before it can be "
                  "judged, and the cost-per-sale line at which to stop it."),
    "hypothesis": ("",
                   "For a test: what the system suspects and what result would confirm or reject it."),
    "raw action": ("raw_action, statistical decision",
                   "The decision from the numbers alone (scale/hold/kill) BEFORE the fatigue and cost-per-sale "
                   "guardrails. If it differs from the final action, a guardrail changed it."),
    "steps": ("decision steps, pipeline, how a decision is made",
              "1 conversations: count sales/failures/excluded. 2 prior: borrow from the parent. 3 posterior: "
              "combine into a score + interval. 4 decision: compare with the baseline -> raw action. 5 money: "
              "spend, revenue, ROAS, cost per sale. 6 fatigue guardrail. 7 cost-per-sale guardrail. 8 final action. "
              "9 allocation: bucket and budget share. 10 test plan: hypothesis and stop rule (tests only). "
              "11 explanation (written after the decision, cannot change it). 12 audit: where decision and money "
              "disagree."),
    "levels": ("campaign, adset, audience, ad, creative",
               "Campaign = the goal (sales/leads/awareness). Ad set = the audience (+ budget). Ad = the creative "
               "the customer saw. Every level is scored and decided separately."),
    "calibration": ("calibrated",
                    "A calibrated score means '70%' happens about 70% of the time. The project measures this on a "
                    "held-out set of conversations it never trained on."),
    "limits": ("what the system does not know, limitations",
               "One merchant, about 180 days, small numbers per ad. It sees only chats that came from ads plus "
               "their outcomes and Meta spend -- not inventory, prices of competitors, footfall, offline sales, or "
               "anything after the data ends. Scores are estimates with uncertainty, not promises. It cannot yet "
               "read what customers wrote (conversation text). It explains stored decisions; it does not predict "
               "the effect of budget changes."),
    "findings": ("audit",
                 "Cases where the decision and the money disagree: scaled but losing money, killed but profitable, "
                 "or a top earner that is not being scaled."),
}

_INDEX = {}
for key, (aliases, _) in TERMS.items():
    _INDEX[key] = key
    for a in filter(None, (x.strip() for x in aliases.split(","))):
        _INDEX[a.lower()] = key


def glossary(term: str) -> dict:
    t = re.sub(r"\s+", " ", term.lower().strip())
    if t in ("all", "*", "list"):
        return {"terms": sorted(TERMS)}
    keys = []
    if t in _INDEX:
        keys.append(_INDEX[t])
    keys += [_INDEX[k] for k in _INDEX
             if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", t) or (len(t) > 3 and t in k)]
    keys += [_INDEX[m] for m in get_close_matches(t, list(_INDEX), n=2, cutoff=0.75)]
    keys = list(dict.fromkeys(keys))[:3]
    if not keys:
        return {"error": f"no glossary entry for '{term}'", "terms": sorted(TERMS)}
    return {k: TERMS[k][1] for k in keys}
