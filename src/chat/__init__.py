"""
Chatbot over the decision-trace database (outputs/trace.db).

Pipeline per question (see docs in each module):

    1 entities.py  match names / ids in the question      (code, no LLM)
    2 gate.py      intent + confidence + rewrite          (LLM or keyword floor)
                   policy: refuse / clarify / not yet / answer
    3 answer       grounded answer with tools              (next stage)
    4 guard        stated action == stored action          (next stage)

The chatbot never decides anything. It reads what the pipeline stored.
"""
