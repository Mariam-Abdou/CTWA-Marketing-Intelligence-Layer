"""
Customers, by customer id only (no names, no phones), built from every
conversation in the data -- from ads, organic and direct.

Segments, judged over the cycles in the data (not the merchant's whole history):
    repeat_buyer          bought in 2+ cycles
    once_never_returned   bought in one cycle, no chat in any later cycle
    once_came_back        bought in one cycle, came back later without buying
    once_last_cycle       bought only in the LAST cycle -- too early to say if
                          they will return, so they are NOT called "never returned"
    never_bought          chatted, never bought
"""

from collections import Counter

REGION = {
    "cairo": "Cairo", "new cairo": "Cairo", "maadi": "Cairo", "heliopolis": "Cairo", "zamalek": "Cairo",
    "nasr city": "Cairo", "giza": "Giza", "mohandessin": "Giza", "dokki": "Giza", "sheikh zayed": "Giza",
    "6 october": "Giza", "alexandria": "Alexandria", "alex": "Alexandria", "mansoura": "Delta",
    "tanta": "Delta",
}
SEGMENT_TEXT = {
    "repeat_buyer": "bought in 2+ cycles",
    "once_never_returned": "bought once, never came back",
    "once_came_back": "bought once, came back later without buying",
    "once_last_cycle": "bought once in the last cycle (too early to tell if they return)",
    "never_bought": "chatted, never bought",
}


def region_of(city):
    return REGION.get((city or "").strip().lower(), "Other" if city else None)


def build_customers(convs: list[dict], product_names: dict) -> list[dict]:
    """convs: one dict per conversation with customer_id, cycle, success,
    platform, ad_id, city, ordered_products (ids), order_value, refunds, net."""
    last_cycle = max((c["cycle"] for c in convs if c.get("cycle") is not None), default=None)
    by = {}
    for c in convs:
        by.setdefault(c["customer_id"], []).append(c)
    out = []
    for cid, xs in by.items():
        xs.sort(key=lambda c: (c.get("cycle") or 0, c.get("started_at") or ""))
        cycles = sorted({c["cycle"] for c in xs if c.get("cycle") is not None})
        bought = sorted({c["cycle"] for c in xs if c.get("success") == 1 and c.get("cycle") is not None})
        if len(bought) >= 2:
            seg = "repeat_buyer"
        elif len(bought) == 1:
            if any(cy > bought[0] for cy in cycles):
                seg = "once_came_back"
            elif bought[0] == last_cycle:
                seg = "once_last_cycle"
            else:
                seg = "once_never_returned"
        else:
            seg = "never_bought"
        city = next((c["city"] for c in reversed(xs) if c.get("city")), None)
        sold = [c for c in xs if c.get("success") == 1]
        products = Counter(p for c in sold for p in (c.get("ordered_products") or []))
        out.append({
            "customer_id": cid, "segment": seg, "city": city, "region": region_of(city),
            "conversations": len(xs), "cycles": cycles, "bought_cycles": bought,
            "first_cycle": cycles[0] if cycles else None, "last_cycle": cycles[-1] if cycles else None,
            "sales": len(sold),
            "order_value": sum(c.get("order_value") or 0 for c in sold),
            "refunds": sum(c.get("refunds") or 0 for c in sold),
            "net_revenue": sum(c.get("net") or 0 for c in sold),
            "products": [product_names.get(p, p) for p, _ in products.most_common()],
            "platforms": sorted({c.get("platform") for c in xs if c.get("platform")}),
            "ad_ids": sorted({c["ad_id"] for c in xs if c.get("ad_id") and c.get("platform") == "meta_ctwa"}),
        })
    return out
