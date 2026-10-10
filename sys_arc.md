    +----------------------------------------------------------------------------------------------------+
    |                                              UI LAYER                                              |
    |                Streamlit App: Dashboard, Report Budget Plan, Explore Tests, Chatbot                |
    +----------------------------------------------------------------------------------------------------+
                             ^                                                  ^
    +------------------------------------------------+  +------------------------------------------------+
    |                  OUTPUT LAYER                  |  |                  CHAT LAYER                    |
    | ---------------------------------------------- |  | ---------------------------------------------- |
    | write_csv / write_plan / print_scoreboard      |  | bot.py     -> ask(): explains, never decides   |
    |                                                |  | budget.py  -> token limit check                |
    | scoreboard_<run>.csv + plan_<run>.json         |  | entities.py-> match ids / names (no AI)        |
    |                                                |  | gate.py    -> intent + rewrite (small LLM)     |
    |                                                |  | answer.py  -> grounded reply (LLM + RO tools)  |
    |                                                |  | tools: get_entity, run_sql, get_conversations, |
    |                                                |  |        get_customers, glossary                 | 
    |                                                |  | guard.py   -> numbers, actions, no predictions |
    |                                                |  | fallback/  -> no-AI reply if any step fails    |
    |                                                |  | log.py     -> chat_log.db                      |
    +------------------------------------------------+  +------------------------------------------------+
                             ^                                                  ^
                             |           +---------------------------------------------------------------+
                             |           |                         STORAGE LAYER                         |
                             |           | ------------------------------------------------------------- |
                             |           | trace.py + storage/trace_db.py -> outputs/trace.db (SQLite)   |
                             |           | - every input, 12-step trail and decision per id              |
                             |           | - customers, findings, daily series                           |
                             |           +---------------------------------------------------------------+
                             |                                                  ^
    +----------------------------------------------------------------------------------------------------+
    |                                        ORCHESTRATION LAYER                                         |
    |                               pipeline.py -> wires every layer below                               |
    +----------------------------------------------------------------------------------------------------+
                ^                         ^                         ^                         ^
    +----------------------+  +----------------------+  +----------------------+  +----------------------+
    |   REASONING LAYER    |  |    DECISION LAYER    |  |     AUDIT LAYER      |  | CONVERSATION SIGNALS |
    | -------------------- |  | -------------------- |  | -------------------- |  | -------------------- |
    | llm.py               |  | outcome_decision.py  |  | audit.py             |  | signals.py ->        |
    | reasoning.py         |  |   -> decide()        |  |   ROAS/spend,        |  |   why a chat did not |
    | hypothesis.py        |  | meta_guardrails.py ->|  |   never feeds        |  |   end in a sale      |
    |                      |  |  fatigue/underperform|  |   decisions          |  |   (rules, no LLM)    |
    | text only,           |  | action.py ->         |  |                      |  |   + evidence line    |
    | never changes        |  |   resolve_action()   |  | findings.py          |  | customers.py ->      |
    | decisions            |  | allocation.py        |  |   decision vs money  |  |   customer segments  |
    |                      |  |   -> 70/30 split     |  |                      |  | money.py ->          |
    |                      |  | explore_selection    |  |                      |  |   revenue + caveat   |
    |                      |  |   -> chosen from     |  |                      |  |                      |
    |                      |  |      explore pool    |  |                      |  | reads chats only,    |
    |                      |  | stop_rules.py ->     |  |                      |  | never feeds decisions|
    |                      |  |  when a test is done |  |                      |  |                      |
    +----------------------+  +----------------------+  +----------------------+  +----------------------+
                ^                         ^                         ^                         ^
    +--------------------------------------------------------------------------+              |
    |                              SCORING LAYER                               |              |
    | ------------------------------------------------------------------------ |              |
    | amounts.py      -> net revenue                                           |              |
    | classifier.py   -> success/fail                                          |              |
    | aggregator.py   -> Raw counts                                            |              |
    | corrector.py    -> Beta-Binomial score (shrunk toward parent)            |              |
    | revenue.py      -> money per level                                       |              |
    +--------------------------------------------------------------------------+              |
                                          ^                                                   |
    +--------------------------------------------------------------------------+              |
    |                             INGESTION LAYER                              |              |
    | ------------------------------------------------------------------------ |              |
    | conversation_loader.py                                                   |--------------+
    | meta_loader.py                                                           |  joined chats,
    | joiner.py  -> group_by_level()                                           |  incl. organic/direct
    | io_utils.py                                                              |
    +--------------------------------------------------------------------------+
                                          ^
    +--------------------------------------------------------------------------+
    |                                DATA LAYER                                |
    | ------------------------------------------------------------------------ |
    | conv_train.json, meta_train.json                                         |
    +--------------------------------------------------------------------------+
