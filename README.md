# Samples Directory — File Reference

This directory contains the 6AM LLM Cohort Marketing Intelligence Layer dataset and processing scripts.
Below is every file, what it contains, and how to read it.

---

## Data Files (input — inside `data/`)

### `data/conversations.json`
**788 conversations** between a Cairo premium-food merchant and customers via WhatsApp. Each record has:
- `id`, `started_at`, `last_message_at` — timing
- `status` — conversation-level state (`closed`, `active`, `pending_handoff`, `stuck_pending`)
- `language` — `ar` / `en` / `mixed` / `arabizi`
- `cycle` — seasonal cycle (1, 2, 3)
- `customer` — `{id, first_name, last_name, phone, country, first_seen_at}`
- `source` — `{platform, ad_id, campaign_id, creative_id, headline}` (ad fields only for `meta_ctwa`)
- `messages[]` — `{direction, text, sent_at, products[]}`
- `outcome` — `{type, order_id, line_items[], total, status_history[]}` — **this is your ground-truth label**
  - Outcome types: `delivered`, `cancelled`, `ghosted`, `refunded`, `stuck_pending`, `adversarial`, `active`

**How to read:** Iterate by conversation. Use `outcome.type` as the target. Ignore `organic`/`direct` source conversations for ad-level analysis (they have no ad metadata).

---

### `data/products.json`
**105 products** in the merchant's catalogue. Each record:
- `id`, `name`, `category`, `price`, `tags[]`
- 19 categories (sugar, spices, coffee, bundles, dairy, etc.)
- 8 products are **bundles** — they have a `contents` array linking to component product IDs

**How to read:** Key by `id` and join from `line_items[].product_id`. For bundles, recursively expand `contents` to get individual items.

---

### `data/meta_data.json`
Ad account metadata with 5 top-level keys:

| Key | Count | Description |
|---|---|---|
| `campaigns` | 12 | Campaigns (id, name, objective, type, status, dates) |
| `adsets` | 26 | Ad sets (id, campaign_id, audience_type, targeting, budget, optimization_goal) |
| `ads` | 40 | Ads (id, adset_id, campaign_id, creative_id, dates) |
| `creatives` | 30 | Creatives (id, theme, angle, object_story_spec) |
| `insights` | ~1243 | Daily performance rows per ad (impressions, clicks, CTR, spend, CPM, etc.) |

**How to read:** This is the join hub.
- `conv.source.ad_id` → `ads[].id` → `adsets[].id` → `campaigns[].id`
- `ads[].creative_id` → `creatives[].id`
- `insights[].ad_id` joins to `ads[].id` (1 ad : many daily insight rows)

---

## Scripts (processing)

### `explore_and_join.py`
**Master script** — profiles the raw data, joins all tables, and exports 5 output files.

What it does in order:
1. **Profiles** each of the 3 input files — prints summary stats (status counts, revenue, top tags, campaign breakdowns)
2. **Joins** every conversation with:
   - Campaign info (name, objective, type, status)
   - Ad & adset info (name, start/end dates, audience type, budget, optimization goal)
   - Creative info (name, theme, angle)
   - Insights (latest daily row per ad: impressions, clicks, CTR, spend, CPM, reach, frequency)
   - Product details (line items expanded — bundles recursively resolved)
3. **Exports** 5 output files (see below)

**How to read:** Run `python3 explore_and_join.py`. It reads from `data/`, writes to the current directory. The join respects the PDF's spine and correctly skips ad metadata for `organic`/`direct` conversations.

---

### `generate_report.py`
Generates an **interactive HTML dashboard** (`report.html`) using Chart.js.

It reads the raw data files, computes the same stats as the exploration report, and renders:
- Outcome doughnut chart
- Order value bar chart
- Platform and language doughnut charts
- Campaign performance table (sortable)
- Creative theme stacked bar chart
- Creative angle order value chart
- Bubble chart (campaign revenue vs conversation volume)

**How to read:** Run `python3 generate_report.py`, then open `report.html` in a browser.

---

### `extract_statuses.py`
Small utility that scans `conversations.json` and extracts every unique status value across three levels:
- `conv.status` — conversation-level state
- `outcome.type` — final order outcome
- `status_history.status` — order lifecycle milestones

**How to read:** Run `python3 extract_statuses.py`. Output goes to stdout and is also saved to `unique_statuses.txt`.

---

## Output Files

### `joined_conversations.json`
**788 enriched conversations** — the output of `explore_and_join.py`. Each record has everything from `conversations.json` **plus** denormalized columns:

| Added column | Source |
|---|---|
| `campaign_name`, `campaign_objective`, `campaign_type`, `campaign_status` | Campaigns join |
| `ad_name`, `adset_id`, `ad_start_date`, `ad_end_date` | Ads join |
| `adset_name`, `adset_audience_type`, `adset_optimization_goal`, `adset_budget` | Ads → Adset join |
| `creative_name`, `creative_theme`, `creative_angle` | Creative join |
| `insights_impressions`, `insights_clicks`, `insights_ctr`, `insights_spend`, `insights_cpm`, `insights_reach`, `insights_frequency`, `insights_link_clicks`, `insights_total` | Latest insight row per ad |
| `insights[]` | Full array of all daily insight rows (for advanced use) |
| `product_details[]` | Resolved line items with bundle expansion, quantity, line totals |

**How to read:** This is your primary analytics table. Each row = one conversation. Filter by `source.platform` when working with ad-level features. Use `product_details` for order composition. Use `customer.id` to group for customer journeys.

---

### `exploration_report.txt`
Structured text report with profiling of all data. Sections:
- Conversations (statuses, outcomes, languages, platforms, cycles, revenue, message stats, customers)
- Products (categories, price range, tags, bundles)
- Meta campaigns (objectives, types, statuses, per-campaign details)
- Adsets (audience types, optimization goals)
- Creatives (themes, angles)
- **Join results** (match rates for each join key)
- **Insights coverage** (how many conversations matched with insights, total spend/impressions/clicks)
- Revenue by outcome type
- Revenue by campaign

**How to read:** Start here for a quick data overview before building models. The join results section tells you how clean each join is.

---

### `campaign_summary.csv`
One row per campaign + a `no_campaign` row for organic/direct conversations:

| Column | Description |
|---|---|
| `campaign_id` | Meta campaign ID or `no_campaign` |
| `campaign_name`, `campaign_type`, `campaign_objective` | Campaign metadata |
| `total_conversations` | Conversations attributed to this campaign |
| `total_revenue` | Sum of `outcome.total` in EGP |
| `delivered`, `cancelled`, `ghosted`, `stuck_pending`, `refunded`, `adversarial`, `active` | Counts per outcome type |

**How to read:** Use to compare campaign performance. The `no_campaign` row captures all organic + direct traffic (171 conversations, 143K EGP).

---

### `outcome_summary.csv`
One row per outcome type:

| Column | Description |
|---|---|
| `outcome_type` | `delivered`, `cancelled`, `ghosted`, etc. |
| `count` | Number of conversations with this outcome |
| `total_revenue` | Sum of `outcome.total` for this type |
| `avg_revenue` | Average order value (`total_revenue / count`) |

**How to read:** Quick view of order health. Note: `active`/`adversarial`/`ghosted` have 0 revenue by definition.

---

### `platform_summary.csv`
One row per traffic source:

| Column | Description |
|---|---|
| `platform` | `meta_ctwa`, `organic`, `direct` |
| `count` | Number of conversations |
| `total_revenue` | Sum of `outcome.total` in EGP |

**How to read:** `meta_ctwa` drives 78% of traffic (617/788) and 80% of revenue. Organic + direct is 171 conversations but 143K EGP — significant.

---

### `report.html`
Interactive Chart.js dashboard. Open in any browser to see:
- Outcome and order value charts
- Platform and language breakdowns
- Campaign performance table
- Creative theme analysis
- Campaign bubble chart

**How to read:** Open in browser. Hover for tooltips. All charts are client-rendered, no server needed.

---

### `unique_statuses.txt`
Three groups of status values:

```
conv.status:        active, closed, pending_handoff, stuck_pending
outcome.type:       active, adversarial, cancelled, delivered, ghosted, refunded, stuck_pending
status_history:     pending → confirmed → processing → shipped → delivered | cancelled | refunded
```

**How to read:** Use this as a dictionary for status values when filtering or encoding categorical features. The `status_history` shows the order lifecycle pipeline.
