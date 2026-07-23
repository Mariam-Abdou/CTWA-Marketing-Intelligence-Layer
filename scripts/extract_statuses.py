import json
import os
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(f"{BASE}/data/conversations.json") as f:
    data = json.load(f)

statuses = set()

for conv in data:
    statuses.add(("conv.status", conv["status"]))
    statuses.add(("outcome.type", conv["outcome"]["type"]))
    for entry in conv["outcome"].get("status_history", []):
        statuses.add(("status_history.status", entry["status"]))

lines = []

lines.append("=== All unique statuses ===")
for src, s in sorted(statuses):
    lines.append(f"  {src}: {s}")

lines.append("")
lines.append("=== Grouped by source ===")
grouped = defaultdict(set)
for src, s in statuses:
    grouped[src].add(s)
for src in sorted(grouped):
    lines.append(f"  {src}: {sorted(grouped[src])}")

output = "\n".join(lines)
print(output)

os.makedirs(f"{BASE}/outputs", exist_ok=True)
with open(f"{BASE}/outputs/unique_statuses.txt", "w") as f:
    f.write(output + "\n")
print(f"\nSaved unique_statuses.txt")
