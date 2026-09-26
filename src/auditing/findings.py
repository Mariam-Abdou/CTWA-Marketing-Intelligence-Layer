"""
Audit layer: where our decision and the money disagree.

Not the same thing as the brief's "merchant-readable findings", which are
written observations for the write-up. These are consistency checks between the
action the pipeline took and what that id actually earned.

It used to rebuild the whole pipeline here -- posteriors, baselines, plans --
just to know what action each id got. That copy drifted the moment the corrector
started fitting its prior from data: this file stayed on PRIOR_STRENGTH=10 while
the pipeline moved to 17.64/16.09, so it was auditing decisions the scoreboard
never made. It now takes the finished rows instead, so there is one source of
truth and nothing to keep in sync.
"""

BREAKEVEN = 1.0
# Ranking cut for "one of your better earners". Same kind of distribution-based
# cut as the median it replaces, but asking a question worth answering: the old
# rule fired on anything above the median, which is half the population by
# construction, and produced findings like "roas=1.07 beats the median of 1.05".
TOP_QUARTILE = 0.75


def flag_findings(rows: list[dict], roas_by_id: dict[str, float]) -> list[dict]:
    """rows are finished scoreboard rows; roas_by_id is the SAME shrunk ROAS the
    allocation used, so a finding quotes the number the decision saw."""
    values = sorted(r for r in (roas_by_id.get(row["id"]) for row in rows) if r is not None)
    if not values:
        return []
    cut = values[min(int(len(values) * TOP_QUARTILE), len(values) - 1)]

    findings = []
    for row in rows:
        roas, action = roas_by_id.get(row["id"]), row["action"]
        if roas is None:
            continue

        if action == "scale" and roas < BREAKEVEN:
            note = (f"Funded to scale, but it loses money: {roas:.2f} EGP back "
                    f"per 1 EGP spent.")
        elif action == "kill" and roas >= BREAKEVEN:
            note = (f"Marked to stop, but it is still profitable: {roas:.2f} EGP "
                    f"back per 1 EGP spent.")
        elif action != "scale" and roas >= cut and roas > BREAKEVEN:
            note = (f"Among the best earners here at {roas:.2f} EGP per 1 EGP spent "
                    f"(top quarter starts at {cut:.2f}), but it is not being scaled.")
        else:
            continue

        findings.append({
            "id": row["id"], "name": row["name"], "action": action,
            "bucket": row["bucket"], "roas": roas, "finding": note,
        })

    findings.sort(key=lambda f: -f["roas"])
    return findings


def print_findings(label: str, findings: list[dict]) -> None:
    if not findings:
        print(f"--- {label}: decisions and money agree ---")
        return
    print(f"--- {label}: {len(findings)} where the decision and the money disagree ---")
    for f in findings:
        print(f"  {f['name'][:44]:<46} {f['finding']}")
