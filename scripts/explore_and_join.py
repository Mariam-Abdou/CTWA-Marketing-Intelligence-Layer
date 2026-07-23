import json
from collections import Counter, defaultdict
import csv
import os

BASE = "/Users/mustafamarzouk/projects/6am-llm-club-project/cappy stuff/samples"

print("=" * 60)
print("LOADING FILES")
print("=" * 60)

with open(f"{BASE}/data/conversations.json") as f:
    conversations = json.load(f)
with open(f"{BASE}/data/products.json") as f:
    products = json.load(f)
with open(f"{BASE}/data/meta_data.json") as f:
    meta = json.load(f)

print(f"  conversations.json: {len(conversations)} records")
print(f"  products.json:      {len(products)} records")
print(f"  meta_data.json:     campaigns={len(meta['campaigns'])}, adsets={len(meta['adsets'])}, ads={len(meta['ads'])}, creatives={len(meta['creatives'])}")

# Build lookup dicts
prod_by_id = {p["id"]: p for p in products}
camp_by_id = {c["id"]: c for c in meta["campaigns"]}
adset_by_id = {a["id"]: a for a in meta["adsets"]}
ad_by_id = {a["id"]: a for a in meta["ads"]}
creative_by_id = {c["id"]: c for c in meta["creatives"]}

# Load & index insights
insights = meta.get("insights", [])
insights_by_ad_id = defaultdict(list)
for ins in insights:
    insights_by_ad_id[ins["ad_id"]].append(ins)

# ============================================================
# SECTION 1: PROFILING
# ============================================================
print("\n" + "=" * 60)
print("PROFILING: conversations.json")
print("=" * 60)

status_counts = Counter()
outcome_counts = Counter()
language_counts = Counter()
platform_counts = Counter()
cycle_counts = Counter()
convs_without_campaign = 0
convs_without_outcome = 0
convs_without_line_items = 0
convs_with_order = 0
total_revenue = 0
order_revenue = 0
cancelled_revenue = 0

for c in conversations:
    status_counts[c["status"]] += 1
    language_counts[c.get("language", "N/A")] += 1
    cycle_counts[c.get("cycle", 0)] += 1
    platform_counts[c["source"]["platform"]] += 1

    if "campaign_id" not in c["source"] or not c["source"]["campaign_id"]:
        convs_without_campaign += 1

    if "outcome" not in c or c["outcome"] is None:
        convs_without_outcome += 1
    else:
        outcome_counts[c["outcome"]["type"]] += 1
        if "line_items" in c["outcome"] and c["outcome"]["line_items"]:
            convs_with_order += 1
        else:
            convs_without_line_items += 1
        if c["outcome"].get("total"):
            total_revenue += c["outcome"]["total"]
            if c["outcome"]["type"] == "cancelled":
                cancelled_revenue += c["outcome"]["total"]
            if c["outcome"]["type"] in ("delivered", "refunded", "stuck_pending"):
                order_revenue += c["outcome"]["total"]

print(f"\nConversation statuses: {dict(status_counts)}")
print(f"Outcome types: {dict(outcome_counts)}")
print(f"Languages: {dict(language_counts)}")
print(f"Source platforms: {dict(platform_counts)}")
print(f"Cycles: {dict(cycle_counts)}")
print(f"Without campaign_id: {convs_without_campaign}")
print(f"Without outcome: {convs_without_outcome}")
print(f"Without line_items (but have outcome): {convs_without_line_items}")
print(f"With orders (line_items present): {convs_with_order}")
print(f"Total revenue (all outcomes): {total_revenue}")
print(f"Cancelled revenue: {cancelled_revenue}")
print(f"Order revenue (delivered+refunded+stuck): {order_revenue}")

# Message stats
msg_lengths = []
msg_count_per_conv = []
for c in conversations:
    msgs = c.get("messages", [])
    msg_count_per_conv.append(len(msgs))
    for m in msgs:
        if m.get("text"):
            msg_lengths.append(len(m["text"]))
if msg_lengths:
    print(f"\nMessage stats:")
    print(f"  Total messages: {len(msg_lengths)}")
    print(f"  Avg per conversation: {sum(msg_count_per_conv)/len(msg_count_per_conv):.1f}")
    print(f"  Avg text length: {sum(msg_lengths)/len(msg_lengths):.0f} chars")

# Customer stats
first_names = Counter()
countries = Counter()
customers_with_email = 0
customers_with_city = 0
for c in conversations:
    cust = c.get("customer", {})
    if cust.get("email"):
        customers_with_email += 1
    if cust.get("city"):
        customers_with_city += 1
    if cust.get("first_name"):
        first_names[cust["first_name"]] += 1
    if cust.get("country"):
        countries[cust["country"]] += 1

print(f"\nCustomer stats:")
print(f"  Unique first names: {len(first_names)}")
print(f"  With email: {customers_with_email}")
print(f"  With city: {customers_with_city}")
print(f"  Countries: {dict(countries)}")
print(f"  Repeat customers (appear in >1 conv): {[n for n, cnt in first_names.items() if cnt > 1]}")

# ============================================================
print("\n" + "=" * 60)
print("PROFILING: products.json")
print("=" * 60)

categories = Counter()
price_range = {"min": float("inf"), "max": 0, "total": 0}
products_with_tags = 0
tag_counter = Counter()
bundle_count = 0

for p in products:
    categories[p["category"]] += 1
    price_range["min"] = min(price_range["min"], p["price"])
    price_range["max"] = max(price_range["max"], p["price"])
    price_range["total"] += p["price"]
    if p.get("tags"):
        products_with_tags += 1
        for t in p["tags"]:
            tag_counter[t] += 1
    if p.get("contents"):
        bundle_count += 1

print(f"\nCategories: {dict(categories)}")
print(f"Price range: {price_range['min']} - {price_range['max']} (avg: {price_range['total']/len(products):.0f})")
print(f"Products with tags: {products_with_tags}/{len(products)}")
print(f"Most common tags: {dict(tag_counter.most_common(10))}")
print(f"Bundle products (have contents): {bundle_count}")

# ============================================================
print("\n" + "=" * 60)
print("PROFILING: meta_data.json")
print("=" * 60)

# Campaigns
camp_objectives = Counter()
camp_types = Counter()
camp_statuses = Counter()
for c in meta["campaigns"]:
    camp_objectives[c["objective"]] += 1
    camp_types[c["campaign_type"]] += 1
    camp_statuses[c["status"]] += 1

print(f"\nCampaign objectives: {dict(camp_objectives)}")
print(f"Campaign types: {dict(camp_types)}")
print(f"Campaign statuses: {dict(camp_statuses)}")
print(f"Campaign names:")
for c in meta["campaigns"]:
    print(f"  [{c['status']:12}] {c['id']}  {c['name']}")

# Adsets
audience_types = Counter()
adset_goals = Counter()
for a in meta["adsets"]:
    audience_types[a["audience_type"]] += 1
    adset_goals[a["optimization_goal"]] += 1

print(f"\nAdset audience types: {dict(audience_types)}")
print(f"Adset optimization goals: {dict(adset_goals)}")

# Creatives
creative_themes = Counter()
creative_angles = Counter()
for cr in meta["creatives"]:
    creative_themes[cr["theme"]] += 1
    creative_angles[cr["angle"]] += 1

print(f"\nCreative themes: {dict(creative_themes)}")
print(f"Creative angles: {dict(creative_angles)}")

# Ads per campaign
ads_per_campaign = Counter()
for ad in meta["ads"]:
    ads_per_campaign[ad["campaign_id"]] += 1
print(f"\nAds per campaign: {dict(ads_per_campaign)}")

# ============================================================
# SECTION 2: JOINING
# ============================================================
print("\n" + "=" * 60)
print("BUILDING JOINED DATA")
print("=" * 60)

def expand_product(prod_id, visited=None):
    """Recursively resolve a product, expanding bundles into their contents."""
    if visited is None:
        visited = set()
    if prod_id in visited:
        return [{"product_id": prod_id, "note": "circular_ref"}]
    visited.add(prod_id)
    
    prod = prod_by_id.get(prod_id)
    if not prod:
        return [{"product_id": prod_id, "note": "not_found"}]
    
    result = {
        "product_id": prod_id,
        "name": prod["name"],
        "category": prod["category"],
        "price": prod["price"],
        "tags": prod.get("tags", []),
    }
    
    if prod.get("contents"):
        # It's a bundle — expand its contents
        children = []
        for item in prod["contents"]:
            children.extend(expand_product(item["product_id"], visited.copy()))
        result["bundle_contents"] = children
        result["bundle_total_price"] = sum(
            ch.get("price", 0) for ch in children if "price" in ch
        )
    
    return [result]

joined = []
join_stats = {
    "total": len(conversations),
    "matched_campaign": 0,
    "matched_ad": 0,
    "matched_creative": 0,
    "matched_insights": 0,
    "platform_meta": 0,
    "platform_organic": 0,
    "platform_direct": 0,
}

for c in conversations:
    row = json.loads(json.dumps(c))  # deep copy
    
    # Campaign join
    campaign_id = c["source"].get("campaign_id")
    campaign = camp_by_id.get(campaign_id) if campaign_id else None
    if campaign:
        row["campaign_name"] = campaign["name"]
        row["campaign_objective"] = campaign["objective"]
        row["campaign_type"] = campaign["campaign_type"]
        row["campaign_status"] = campaign["status"]
        join_stats["matched_campaign"] += 1
    else:
        row["campaign_name"] = None
        row["campaign_objective"] = None
        row["campaign_type"] = None
        row["campaign_status"] = None
    
    # Ad join -> then adset join
    ad_id = c["source"].get("ad_id")
    ad = ad_by_id.get(ad_id) if ad_id else None
    if ad:
        row["ad_name"] = ad["name"]
        row["adset_id"] = ad.get("adset_id")
        row["ad_start_date"] = ad.get("start_date")
        row["ad_end_date"] = ad.get("end_date")
        join_stats["matched_ad"] += 1
        
        adset = adset_by_id.get(ad.get("adset_id"))
        if adset:
            row["adset_name"] = adset["name"]
            row["adset_audience_type"] = adset["audience_type"]
            row["adset_optimization_goal"] = adset["optimization_goal"]
            row["adset_budget"] = adset.get("daily_budget")
        else:
            row["adset_name"] = None
            row["adset_audience_type"] = None
            row["adset_optimization_goal"] = None
            row["adset_budget"] = None
    else:
        row["ad_name"] = None
        row["adset_id"] = None
        row["ad_start_date"] = None
        row["ad_end_date"] = None
        row["adset_name"] = None
        row["adset_audience_type"] = None
        row["adset_optimization_goal"] = None
        row["adset_budget"] = None
    
    # Creative join
    creative_id = c["source"].get("creative_id")
    creative = creative_by_id.get(creative_id) if creative_id else None
    if creative:
        row["creative_name"] = creative["name"]
        row["creative_theme"] = creative["theme"]
        row["creative_angle"] = creative["angle"]
        join_stats["matched_creative"] += 1
    else:
        row["creative_name"] = None
        row["creative_theme"] = None
        row["creative_angle"] = None
    
    # Insights join (by ad_id)
    row["insights"] = []
    if ad_id:
        ad_insights = insights_by_ad_id.get(ad_id, [])
        if ad_insights:
            ad_insights_sorted = sorted(ad_insights, key=lambda x: x.get("date_start", ""))
            latest = ad_insights_sorted[-1]
            row["insights_impressions"] = latest.get("impressions")
            row["insights_clicks"] = latest.get("clicks")
            row["insights_ctr"] = latest.get("ctr")
            row["insights_spend"] = latest.get("spend")
            row["insights_link_clicks"] = latest.get("link_clicks")
            row["insights_cpm"] = latest.get("cpm")
            row["insights_reach"] = latest.get("reach")
            row["insights_frequency"] = latest.get("frequency")
            row["insights_total"] = len(ad_insights)
            row["insights"] = ad_insights  # full array for advanced use
            join_stats["matched_insights"] += 1

    # Platform tracking
    plat = c["source"]["platform"]
    if plat == "meta_ctwa":
        join_stats["platform_meta"] += 1
    elif plat == "organic":
        join_stats["platform_organic"] += 1
    elif plat == "direct":
        join_stats["platform_direct"] += 1
    
    # Product details for line items (with bundle expansion)
    product_details = []
    if "outcome" in c and c["outcome"] and "line_items" in c["outcome"]:
        for li in c["outcome"]["line_items"]:
            expanded = expand_product(li["product_id"])
            for e in expanded:
                e["quantity"] = li["quantity"]
                e["line_total"] = li["quantity"] * e.get("price", 0)
            product_details.extend(expanded)
    row["product_details"] = product_details
    
    joined.append(row)

print(f"\nJoin results:")
for k, v in join_stats.items():
    print(f"  {k}: {v}")

# ============================================================
# SECTION 3: SAVE FILES
# ============================================================
print("\n" + "=" * 60)
print("SAVING RESULTS")
print("=" * 60)

os.makedirs(f"{BASE}/outputs", exist_ok=True)

# 1. Joined conversations
with open(f"{BASE}/outputs/joined_conversations.json", "w") as f:
    json.dump(joined, f, indent=2, ensure_ascii=False)
print(f"  Saved joined_conversations.json ({len(joined)} records)")

# 2. Exploration report
report_lines = []
def w(line=""):
    report_lines.append(line)

w("=" * 70)
w("DATA EXPLORATION REPORT — WhatsApp Ad Analytics")
w("=" * 70)

w("\n--- CONVERSATIONS ---")
w(f"  Total records: {len(conversations)}")
w(f"  Statuses: {dict(status_counts)}")
w(f"  Outcome types: {dict(outcome_counts)}")
w(f"  Languages: {dict(language_counts)}")
w(f"  Source platforms: {dict(platform_counts)}")
w(f"  Cycles: {dict(cycle_counts)}")
w(f"  Without campaign_id: {convs_without_campaign}")
w(f"  With orders (line_items): {convs_with_order}")
w(f"  Without line_items (ghosted/adversarial/none): {convs_without_line_items}")
w(f"  Total revenue (all outcomes): {total_revenue} EGP")
w(f"  Order revenue (delivered+refunded+stuck): {order_revenue} EGP")
w(f"  Cancelled revenue: {cancelled_revenue} EGP")
w(f"  Avg messages/conv: {sum(msg_count_per_conv)/len(msg_count_per_conv):.1f}")
w(f"  Avg message length: {sum(msg_lengths)/len(msg_lengths):.0f} chars")
w(f"  Customers with email: {customers_with_email}")
w(f"  Customers with city: {customers_with_city}")
w(f"  Countries: {dict(countries)}")

w("\n--- PRODUCTS ---")
w(f"  Total products: {len(products)}")
w(f"  Categories: {dict(categories)}")
w(f"  Price range: {price_range['min']} - {price_range['max']} EGP (avg {price_range['total']/len(products):.0f})")
w(f"  Bundle products: {bundle_count}")
w(f"  Products with tags: {products_with_tags}")
w(f"  Top tags: {dict(tag_counter.most_common(10))}")

w("\n--- META CAMPAIGNS ---")
w(f"  Total campaigns: {len(meta['campaigns'])}")
w(f"  Objectives: {dict(camp_objectives)}")
w(f"  Types: {dict(camp_types)}")
w(f"  Statuses: {dict(camp_statuses)}")
for c in meta["campaigns"]:
    w(f"    {c['name']:40s} | type={c['campaign_type']:15s} objective={c['objective']:20s} status={c['status']}")

w("\n--- META ADSETS ---")
w(f"  Total adsets: {len(meta['adsets'])}")
w(f"  Audience types: {dict(audience_types)}")
w(f"  Optimization goals: {dict(adset_goals)}")

w("\n--- META CREATIVES ---")
w(f"  Total creatives: {len(meta['creatives'])}")
w(f"  Themes: {dict(creative_themes)}")
w(f"  Angles: {dict(creative_angles)}")

w("\n--- JOIN RESULTS ---")
for k, v in join_stats.items():
    w(f"  {k}: {v}")

# Revenue by outcome type
w("\n--- INSIGHTS COVERAGE ---")
convs_with_insights = sum(1 for j in joined if j.get("insights"))

def safe_num(v):
    try:
        return float(v) if v is not None else 0
    except (ValueError, TypeError):
        return 0

w(f"  Conversations with insights: {convs_with_insights}")
w(f"  Total spend (convs with insights): {sum(safe_num(j.get('insights_spend')) for j in joined):.2f}")
w(f"  Total impressions: {sum(int(safe_num(j.get('insights_impressions'))) for j in joined)}")
w(f"  Total clicks: {sum(int(safe_num(j.get('insights_clicks'))) for j in joined)}")
w(f"  Total link clicks: {sum(int(safe_num(j.get('insights_link_clicks'))) for j in joined)}")

w("\n--- REVENUE BY OUTCOME ---")
outcome_revenue = defaultdict(float)
for c in conversations:
    if "outcome" in c and c["outcome"] and c["outcome"].get("total"):
        outcome_revenue[c["outcome"]["type"]] += c["outcome"]["total"]
for otype, rev in sorted(outcome_revenue.items()):
    w(f"  {otype:20s}: {rev:10.0f} EGP ({rev/total_revenue*100:.1f}%)")

w("\n--- REVENUE BY CAMPAIGN ---")
campaign_revenue = defaultdict(lambda: {"convs": 0, "revenue": 0, "outcomes": Counter()})
for c in joined:
    cname = c.get("campaign_name") or f"(no campaign / {c['source']['platform']})"
    camp_id = c["source"].get("campaign_id") or "none"
    key = f"{cname} ({camp_id})"
    campaign_revenue[key]["convs"] += 1
    if "outcome" in c and c["outcome"] and c["outcome"].get("total"):
        campaign_revenue[key]["revenue"] += c["outcome"]["total"]
        campaign_revenue[key]["outcomes"][c["outcome"]["type"]] += 1

for cname, stats in sorted(campaign_revenue.items(), key=lambda x: -x[1]["revenue"]):
    w(f"  {cname:60s} | convs={stats['convs']:2d} rev={stats['revenue']:6.0f} outcomes={dict(stats['outcomes'])}")

with open(f"{BASE}/outputs/exploration_report.txt", "w") as f:
    f.write("\n".join(report_lines))
print(f"  Saved exploration_report.txt ({len(report_lines)} lines)")

# 3. Campaign summary CSV
fieldnames = ["campaign_id", "campaign_name", "campaign_type", "campaign_objective",
              "total_conversations", "total_revenue",
              "delivered", "cancelled", "ghosted", "stuck_pending", "refunded", "adversarial", "active"]
campaign_rows = {}
for c in joined:
    cid = c["source"].get("campaign_id") or "no_campaign"
    if cid not in campaign_rows:
        campaign_rows[cid] = {f: c.get(f) or "" for f in ["campaign_name", "campaign_type", "campaign_objective"]}
        campaign_rows[cid].update({f: 0 for f in fieldnames if f not in ("campaign_id", "campaign_name", "campaign_type", "campaign_objective")})
        campaign_rows[cid]["campaign_id"] = cid
        campaign_rows[cid]["total_conversations"] = 0
        campaign_rows[cid]["total_revenue"] = 0
    
    campaign_rows[cid]["total_conversations"] += 1
    oc = c.get("outcome", {})
    if oc and oc.get("type"):
        campaign_rows[cid][oc["type"]] += 1
    if oc and oc.get("total"):
        campaign_rows[cid]["total_revenue"] += oc["total"]

with open(f"{BASE}/outputs/campaign_summary.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for row in campaign_rows.values():
        writer.writerow(row)
print(f"  Saved campaign_summary.csv ({len(campaign_rows)} rows)")

# 4. Outcome summary CSV
outcome_fieldnames = ["outcome_type", "count", "total_revenue", "avg_revenue"]
with open(f"{BASE}/outputs/outcome_summary.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=outcome_fieldnames)
    writer.writeheader()
    for otype, cnt in sorted(outcome_counts.items()):
        rev = outcome_revenue[otype]
        writer.writerow({
            "outcome_type": otype,
            "count": cnt,
            "total_revenue": rev,
            "avg_revenue": round(rev / cnt, 1) if cnt else 0
        })
print(f"  Saved outcome_summary.csv")

# 5. Platform summary CSV
platform_fieldnames = ["platform", "count", "total_revenue"]
with open(f"{BASE}/outputs/platform_summary.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=platform_fieldnames)
    writer.writeheader()
    for plat in sorted(platform_counts.keys()):
        rev = sum(
            c.get("outcome", {}).get("total", 0)
            for c in conversations
            if c["source"]["platform"] == plat and c.get("outcome")
        )
        writer.writerow({
            "platform": plat,
            "count": platform_counts[plat],
            "total_revenue": rev
        })
print(f"  Saved platform_summary.csv")

print("\n" + "=" * 60)
print("ALL DONE")
print("=" * 60)
