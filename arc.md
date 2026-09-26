    +--------------------------------------------------------------------+
    |                               UI LAYER                              |
    |                            Streamlit App                            |
    +--------------------------------------------------------------------+
                                       ^
    +--------------------------------------------------------------------+
    |                            OUTPUT LAYER                             |
    |             write_csv / write_plan / print_scoreboard               |
    +--------------------------------------------------------------------+
                                       ^
    +--------------------------------------------------------------------+
    |                         ORCHESTRATION LAYER                         |
    |                       wires every layer below                       |
    +--------------------------------------------------------------------+
                                       ^
+----------------------+   +----------------------+   +----------------------+
|   REASONING LAYER    |   |    DECISION LAYER    |   |     AUDIT LAYER      |
| -------------------- |   | -------------------- |   | -------------------- |
|        llm.py        |   |  outcome_decision.py |   |       audit.py       |
|     reasoning.py     |   |     -> decide()      |   |     ROAS/spend,      |
|    hypothesis.py     |   | meta_guardrails.py ->|   |      never feeds     |
|                      |   | fatigue/underperform |   |       decisions      |
|      text only,      |   |      action.py ->    |   |                      |
|    never changes     |   |   resolve_action()   |   |     findings.py      |
|      decisions       |   |    allocation.py     |   |   decision vs money  |
|                      |   |    -> 70/30 split    |   |                      |
|                      |   |   explore_selection  |   |                      |
|                      |   |     -> chosen from   |   |                      |
|                      |   |      explore pool    |   |                      |
|                      |   |    stop_rules.py ->  |   |                      |
|                      |   |  when a test is done |   |                      |
+----------------------+   +----------------------+   +----------------------+
                                       ^
                 +------------------------------------------+
                 |              SCORING LAYER               |
                 | ---------------------------------------- |
                 |     amounts.py       -> net revenue      |
                 |     classifier.py    -> success/fail     |
                 |     aggregator.py    -> Raw counts       |
                 |     corrector.py     -> Beta-Binomial    |
                 |              score (shrunk toward parent)|
                 |     revenue.py       -> money per level  |
                 +------------------------------------------+
                                       ^
                 +------------------------------------------+
                 |             INGESTION LAYER              |
                 | ---------------------------------------- |
                 |          conversation_loader.py          |
                 |          meta_loader.py                  |
                 |          joiner.py  -> group_by_level()  |
                 |          io_utils.py                     |
                 +------------------------------------------+
                                       ^
                 +------------------------------------------+
                 |                  DATA LAYER              |
                 |     conv_train.json, meta_train.json     |
                 +------------------------------------------+