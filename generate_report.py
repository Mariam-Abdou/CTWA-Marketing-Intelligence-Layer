import json
from collections import Counter, defaultdict
import os

BASE = "/Users/mustafamarzouk/projects/6am-llm-club-project/cappy stuff/samples"

with open(f"{BASE}/data/conversations.json") as f:
    conversations = json.load(f)
with open(f"{BASE}/data/products.json") as f:
    products = json.load(f)
with open(f"{BASE}/data/meta_data.json") as f:
    meta = json.load(f)

prod_by_id = {p["id"]: p for p in products}
camp_by_id = {c["id"]: c for c in meta["campaigns"]}
adset_by_id = {a["id"]: a for a in meta["adsets"]}
ad_by_id = {a["id"]: a for a in meta["ads"]}
creative_by_id = {c["id"]: c for c in meta["creatives"]}

# --- Stats ---
status_counts = Counter()
outcome_counts = Counter()
language_counts = Counter()
platform_counts = Counter()
cycle_counts = Counter()
outcome_revenue = defaultdict(float)
campaign_stats = defaultdict(lambda: {"convs": 0, "revenue": 0, "outcomes": Counter()})
total_revenue_all = 0
msg_count_per_conv = []

for c in conversations:
    status_counts[c["status"]] += 1
    language_counts[c.get("language", "N/A")] += 1
    cycle_counts[c.get("cycle", 0)] += 1
    platform_counts[c["source"]["platform"]] += 1
    msgs = c.get("messages", [])
    msg_count_per_conv.append(len(msgs))

    if "outcome" in c and c["outcome"]:
        outcome_counts[c["outcome"]["type"]] += 1
        if c["outcome"].get("total"):
            outcome_revenue[c["outcome"]["type"]] += c["outcome"]["total"]
            total_revenue_all += c["outcome"]["total"]

    cname = camp_by_id[c["source"].get("campaign_id", "")]["name"] if c["source"].get("campaign_id") in camp_by_id else f"(no campaign / {c['source']['platform']})"
    campaign_stats[cname]["convs"] += 1
    ot = c.get("outcome", {}) or {}
    if ot.get("type"):
        campaign_stats[cname]["outcomes"][ot["type"]] += 1
    if ot.get("total"):
        campaign_stats[cname]["revenue"] += ot["total"]

outcome_order = ["delivered", "cancelled", "ghosted", "stuck_pending", "refunded", "adversarial", "active"]
outcome_colors = {
    "delivered": "#22c55e", "cancelled": "#ef4444", "ghosted": "#f59e0b",
    "stuck_pending": "#f97316", "refunded": "#8b5cf6", "adversarial": "#dc2626",
    "active": "#3b82f6"
}

avg_msgs = sum(msg_count_per_conv) / len(msg_count_per_conv)

# --- HTML ---
html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WhatsApp Ad Analytics — Campaign Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #0f172a;
  color: #e2e8f0;
  padding: 32px 24px;
}}
h1 {{ font-size: 28px; font-weight: 700; color: #f8fafc; margin-bottom: 4px; }}
h2 {{ font-size: 20px; font-weight: 600; color: #94a3b8; margin-bottom: 20px; }}
h3 {{ font-size: 15px; font-weight: 600; color: #cbd5e1; margin-bottom: 12px; }}
.subtitle {{ color: #64748b; font-size: 14px; margin-bottom: 32px; }}
.stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 32px; }}
.stat-card {{
  background: #1e293b; border-radius: 12px; padding: 16px; border: 1px solid #334155;
  transition: transform 0.1s;
}}
.stat-card:hover {{ transform: translateY(-1px); }}
.stat-card .value {{ font-size: 28px; font-weight: 700; color: #f8fafc; line-height: 1.2; }}
.stat-card .label {{ font-size: 12px; font-weight: 500; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }}
.stat-card .sub {{ font-size: 13px; color: #94a3b8; margin-top: 4px; }}
.section {{ margin-bottom: 40px; }}
.chart-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
@media (max-width: 800px) {{ .chart-row {{ grid-template-columns: 1fr; }} }}
.chart-box {{
  background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155;
}}
.chart-box.full {{ grid-column: 1 / -1; }}
canvas {{ max-height: 320px; }}
table {{
  width: 100%; border-collapse: separate; border-spacing: 0;
  font-size: 13px;
}}
thead th {{
  background: #1e293b; color: #94a3b8; font-weight: 600; text-align: left;
  padding: 10px 12px; position: sticky; top: 0; border-bottom: 2px solid #334155;
}}
tbody td {{
  padding: 10px 12px; border-bottom: 1px solid #1e293b;
}}
tbody tr:hover td {{ background: #1e293b; }}
.table-wrap {{
  background: #0f172a; border-radius: 12px; border: 1px solid #334155; overflow-x: auto;
  max-height: 480px; overflow-y: auto;
}}
.badge {{
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 600;
}}
.badge-delivered {{ background: #052e16; color: #86efac; }}
.badge-cancelled {{ background: #450a0a; color: #fca5a5; }}
.badge-ghosted {{ background: #451a03; color: #fcd34d; }}
.badge-stuck_pending {{ background: #431407; color: #fdba74; }}
.badge-refunded {{ background: #2e1065; color: #c4b5fd; }}
.badge-adversarial {{ background: #7f1d1d; color: #fecaca; }}
.badge-active {{ background: #1e3a5f; color: #93c5fd; }}
.badge-positive {{ background: #052e16; color: #86efac; }}
.badge-negative {{ background: #450a0a; color: #fca5a5; }}
.badge-neutral {{ background: #451a03; color: #fcd34d; }}
.outcome-dot {{
  display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px;
}}
.num {{ font-variant-numeric: tabular-nums; text-align: right; }}
.footer {{ text-align: center; color: #475569; font-size: 12px; margin-top: 48px; padding-top: 16px; border-top: 1px solid #1e293b; }}
</style>
</head>
<body>

<h1>WhatsApp Ad Analytics</h1>
<p class="subtitle">Campaign performance dashboard — raw conversation data transformed into marketing intelligence</p>

<div class="stat-grid">
  <div class="stat-card"><div class="value">{len(conversations)}</div><div class="label">Conversations</div></div>
  <div class="stat-card"><div class="value">{outcome_counts.get('delivered', 0)}</div><div class="label">Delivered</div><div class="sub">{outcome_counts.get('delivered', 0)/len(conversations)*100:.0f}% of total</div></div>
  <div class="stat-card"><div class="value">{total_revenue_all:,}</div><div class="label">Total Order Value (EGP)</div></div>
  <div class="stat-card"><div class="value">{outcome_counts.get('ghosted', 0)}</div><div class="label">Ghosted</div><div class="sub">{outcome_counts.get('ghosted', 0)/len(conversations)*100:.0f}% wasted</div></div>
  <div class="stat-card"><div class="value">{outcome_counts.get('cancelled', 0)}</div><div class="label">Cancelled</div></div>
  <div class="stat-card"><div class="value">{platform_counts.get('meta_ctwa', 0)}</div><div class="label">Meta Ad Leads</div><div class="sub">{platform_counts.get('meta_ctwa', 0)/len(conversations)*100:.0f}% of traffic</div></div>
  <div class="stat-card"><div class="value">{avg_msgs:.1f}</div><div class="label">Avg Messages / Conv</div></div>
  <div class="stat-card"><div class="value">{platform_counts.get('organic', 0) + platform_counts.get('direct', 0)}</div><div class="label">Organic + Direct</div></div>
</div>

<div class="section">
  <h2>Outcome Breakdown</h2>
  <div class="chart-row">
    <div class="chart-box">
      <h3>Conversations by Outcome</h3>
      <canvas id="outcomeChart"></canvas>
    </div>
    <div class="chart-box">
      <h3>Order Value by Outcome (EGP)</h3>
      <canvas id="orderValueChart"></canvas>
    </div>
  </div>
</div>

<div class="section">
  <h2>Traffic Sources</h2>
  <div class="chart-row">
    <div class="chart-box">
      <h3>Conversations by Platform</h3>
      <canvas id="platformChart"></canvas>
    </div>
    <div class="chart-box">
      <h3>Conversations by Language</h3>
      <canvas id="languageChart"></canvas>
    </div>
  </div>
</div>

<div class="section">
  <h2>Campaign Performance</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Campaign</th>
          <th style="text-align:right">Convs</th>
          <th style="text-align:right">Gross Order Value (EGP)</th>
          <th style="text-align:right">Delivered</th>
          <th style="text-align:right">Cancelled</th>
          <th style="text-align:right">Ghosted</th>
          <th style="text-align:right">Stuck</th>
          <th style="text-align:right">Refunded</th>
          <th style="text-align:right">Adv.</th>
          <th style="text-align:right">Active</th>
        </tr>
      </thead>
      <tbody>
'''

for cname, stats in sorted(campaign_stats.items(), key=lambda x: -x[1]["revenue"]):
    html += f'''        <tr>
          <td>{cname[:55]}</td>
          <td class="num">{stats["convs"]}</td>
          <td class="num">{stats["revenue"]:,.0f}</td>
          <td class="num">{stats["outcomes"].get("delivered", 0)}</td>
          <td class="num">{stats["outcomes"].get("cancelled", 0)}</td>
          <td class="num">{stats["outcomes"].get("ghosted", 0)}</td>
          <td class="num">{stats["outcomes"].get("stuck_pending", 0)}</td>
          <td class="num">{stats["outcomes"].get("refunded", 0)}</td>
          <td class="num">{stats["outcomes"].get("adversarial", 0)}</td>
          <td class="num">{stats["outcomes"].get("active", 0)}</td>
        </tr>
'''
html += '''      </tbody>
    </table>
  </div>
</div>

<div class="section">
  <h2>Ad Performance by Creative Theme</h2>
  <div class="chart-row">
    <div class="chart-box">
      <h3>Outcomes by Creative Theme</h3>
      <canvas id="creativeChart"></canvas>
    </div>
    <div class="chart-box">
      <h3>Order Value by Creative Angle</h3>
      <canvas id="angleChart"></canvas>
    </div>
  </div>
</div>

<div class="section">
  <h2>Campaigns at a Glance</h2>
  <div class="chart-box full">
    <h3>Order Value vs Conversation Volume</h3>
    <canvas id="bubbleChart" style="max-height: 400px;"></canvas>
  </div>
</div>

<div class="footer">
  Generated from conversations.json &middot; products.json &middot; meta_data.json &mdash; ''' + f"{len(conversations)} conversations, {len(products)} products, {len(meta['campaigns'])} campaigns" + '''
</div>
''' + '''
<script>
// --- Outcome Chart ---
const outcomeCtx = document.getElementById('outcomeChart').getContext('2d');
new Chart(outcomeCtx, {
  type: 'doughnut',
  data: {
    labels: ['''

for o in outcome_order:
    if outcome_counts.get(o, 0) > 0:
        html += f"'{o}', "
html += '''],\n    datasets: [{\n      data: ['''
for o in outcome_order:
    if outcome_counts.get(o, 0) > 0:
        html += f"{outcome_counts[o]}, "
html += '''],\n      backgroundColor: ['''
for o in outcome_order:
    if outcome_counts.get(o, 0) > 0:
        html += f"'{outcome_colors[o]}', "
html += '''],\n      borderWidth: 0\n    }]\n  },\n  options: {\n    responsive: true,\n    plugins: {\n      legend: { position: 'right', labels: { color: '#94a3b8' } }\n    }\n  }\n});\n\n'''

# --- Order Value Chart ---
html += '''const orderValueCtx = document.getElementById('orderValueChart').getContext('2d');
new Chart(orderValueCtx, {
  type: 'bar',
  data: {
    labels: ['''
for o in outcome_order:
    if outcome_revenue.get(o, 0) > 0:
        html += f"'{o}', "
html += '''],\n    datasets: [{\n      label: 'Order Value (EGP)',\n      data: ['''
for o in outcome_order:
    if outcome_revenue.get(o, 0) > 0:
        html += f"{outcome_revenue[o]}, "
html += '''],\n      backgroundColor: ['''
for o in outcome_order:
    if outcome_revenue.get(o, 0) > 0:
        html += f"'{outcome_colors[o]}', "
html += '''],\n      borderRadius: 6\n    }]\n  },\n  options: {\n    responsive: true,\n    plugins: {\n      legend: { display: false },\n    },\n    scales: {\n      y: { ticks: { color: '#64748b' }, grid: { color: '#1e293b' } },\n      x: { ticks: { color: '#94a3b8' } }\n    }\n  }\n});\n\n'''

# --- Platform Chart ---
html += '''const platformCtx = document.getElementById('platformChart').getContext('2d');
new Chart(platformCtx, {
  type: 'doughnut',
  data: {
    labels: ['''
for p, cnt in sorted(platform_counts.items()):
    html += f"'{p} ({cnt})', "
html += '''],\n    datasets: [{\n      data: ['''
for p, cnt in sorted(platform_counts.items()):
    html += f"{cnt}, "
html += '''],\n      backgroundColor: ['#3b82f6', '#22c55e', '#f59e0b'],\n      borderWidth: 0\n    }]\n  },\n  options: {\n    responsive: true,\n    plugins: {\n      legend: { position: 'right', labels: { color: '#94a3b8' } }\n    }\n  }\n});\n\n'''

# --- Language Chart ---
lang_colors = {"ar": "#f59e0b", "en": "#3b82f6", "mixed": "#8b5cf6", "arabizi": "#ec4899"}
html += '''const languageCtx = document.getElementById('languageChart').getContext('2d');
new Chart(languageCtx, {
  type: 'doughnut',
  data: {
    labels: ['''
for l, cnt in sorted(language_counts.items()):
    html += f"'{l} ({cnt})', "
html += '''],\n    datasets: [{\n      data: ['''
for l, cnt in sorted(language_counts.items()):
    html += f"{cnt}, "
html += '''],\n      backgroundColor: ['''
for l in sorted(language_counts.keys()):
    html += f"'{lang_colors.get(l, '#94a3b8')}', "
html += '''],\n      borderWidth: 0\n    }]\n  },\n  options: {\n    responsive: true,\n    plugins: {\n      legend: { position: 'right', labels: { color: '#94a3b8' } }\n    }\n  }\n});\n\n'''

# --- Creative Theme analysis ---
theme_outcomes = defaultdict(lambda: Counter())
theme_revenue = defaultdict(float)
for c in conversations:
    if c["source"]["platform"] != "meta_ctwa":
        continue
    creative = creative_by_id.get(c["source"].get("creative_id", ""))
    if not creative:
        continue
    theme = creative["theme"]
    oc = c.get("outcome", {})
    if oc and oc.get("type"):
        theme_outcomes[theme][oc["type"]] += 1
    if oc and oc.get("total"):
        theme_revenue[creative.get("angle", "unknown")] += oc["total"]

html += '''const creativeCtx = document.getElementById('creativeChart').getContext('2d');
new Chart(creativeCtx, {
  type: 'bar',
  data: {
    labels: ['''
for t in sorted(theme_outcomes.keys()):
    html += f"'{t}', "
html += '''],\n    datasets: ['''
for o in outcome_order:
    has_data = any(theme_outcomes[t].get(o, 0) > 0 for t in theme_outcomes)
    if has_data:
        html += f"{{ label: '{o}', data: ["
        for t in sorted(theme_outcomes.keys()):
            html += f"{theme_outcomes[t].get(o, 0)}, "
        html += f"], backgroundColor: '{outcome_colors[o]}', borderRadius: 3 }}, "
html += ''']\n  },\n  options: {\n    responsive: true,\n    plugins: { legend: { position: 'right', labels: { color: '#94a3b8' } } },\n    scales: {\n      y: { stacked: true, ticks: { color: '#64748b' }, grid: { color: '#1e293b' } },\n      x: { stacked: true, ticks: { color: '#94a3b8' } }\n    }\n  }\n});\n\n'''

# --- Angle Order Value ---
top_angles = sorted(theme_revenue.items(), key=lambda x: -x[1])[:10]
html += '''const angleCtx = document.getElementById('angleChart').getContext('2d');
new Chart(angleCtx, {
  type: 'bar',
  data: {
    labels: ['''
for a, rev in top_angles:
    html += f"'{a}', "
html += '''],\n    datasets: [{\n      label: 'Order Value (EGP)',\n      data: ['''
for a, rev in top_angles:
    html += f"{rev}, "
html += '''],\n      backgroundColor: '#3b82f6',\n      borderRadius: 6\n    }]\n  },\n  options: {\n    indexAxis: 'y',\n    responsive: true,\n    plugins: { legend: { display: false } },\n    scales: {\n      y: { ticks: { color: '#94a3b8' } },\n      x: { ticks: { color: '#64748b' }, grid: { color: '#1e293b' } }\n    }\n  }\n});\n\n'''

# --- Bubble chart ---
html += '''const bubbleCtx = document.getElementById('bubbleChart').getContext('2d');
new Chart(bubbleCtx, {
  type: 'bubble',
  data: {
    datasets: ['''
for cname, stats in sorted(campaign_stats.items(), key=lambda x: -x[1]["revenue"])[:7]:
    r = max(stats['revenue'] / 10000, 5)
    html += f"{{ label: '{cname[:30]}', data: [{{ x: {stats['convs']}, y: {stats['revenue']:.0f}, r: {r} }}], backgroundColor: '#3b82f660', borderColor: '#3b82f6', borderWidth: 2 }}, "

html += ''']\n  },\n  options: {\n    responsive: true,\n    plugins: {\n      legend: { display: false },\n      tooltip: {\n        callbacks: {\n          label: function(ctx) { return ctx.dataset.label + ': ' + ctx.raw.x + ' convs, ' + ctx.raw.y.toLocaleString() + ' EGP'; }\n        }\n      }\n    },\n    scales: {\n      y: { title: { display: true, text: 'Order Value (EGP)', color: '#64748b' }, ticks: { color: '#64748b' }, grid: { color: '#1e293b' } },\n      x: { title: { display: true, text: 'Conversations', color: '#64748b' }, ticks: { color: '#64748b' }, grid: { color: '#1e293b' } }\n    }\n  }\n});
</script>
</body>
</html>'''

with open(f"{BASE}/report.html", "w") as f:
    f.write(html)

print(f"Saved report.html ({len(html):,} bytes)")
