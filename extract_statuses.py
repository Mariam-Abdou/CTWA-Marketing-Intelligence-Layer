import json

with open("/Users/mustafamarzouk/projects/6am-llm-club-project/cappy stuff/samples/data/conversations.json") as f:
    data = json.load(f)

statuses = set()

for conv in data:
    statuses.add(("conv.status", conv["status"]))
    statuses.add(("outcome.type", conv["outcome"]["type"]))
    for entry in conv["outcome"].get("status_history", []):
        statuses.add(("status_history.status", entry["status"]))

print("=== All unique statuses ===")
for src, s in sorted(statuses):
    print(f"  {src}: {s}")

print()
print("=== Grouped by source ===")
from collections import defaultdict
grouped = defaultdict(set)
for src, s in statuses:
    grouped[src].add(s)
for src in sorted(grouped):
    print(f"  {src}: {sorted(grouped[src])}")
