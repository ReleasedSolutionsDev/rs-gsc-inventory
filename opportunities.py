"""Generate opportunity lists from the latest perf snapshot.

Three views:
  - near_page_1: queries ranking 8-20 with real impressions — smallest push wins
  - low_ctr:    queries on page 1 but getting under-CTR — title/description work
  - losing_pages: pages whose clicks dropped meaningfully vs the prior 14 days

Reads perf-latest.json (produced by perf.py). Writes opportunities-YYYY-MM-DD.json
plus opportunities-latest.json.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Thresholds — tune here without touching logic
NEAR_PAGE1_MIN_POS = 8
NEAR_PAGE1_MAX_POS = 20
NEAR_PAGE1_MIN_IMP = 30

LOW_CTR_MAX_POS = 10
LOW_CTR_MIN_IMP = 50
LOW_CTR_MAX_CTR = 0.02  # under 2 % on page-1 is a snippet problem

LOSING_MIN_PRIOR_CLICKS = 10
LOSING_DECLINE_PCT = 30  # 30 %+ drop


def main() -> int:
    src = HERE / "perf-latest.json"
    if not src.exists():
        print("ERROR: perf-latest.json missing. Run perf.py first.", file=sys.stderr)
        return 2
    perf = json.loads(src.read_text())

    # NEAR PAGE 1: queries at pos 8-20 with impressions >= threshold
    near = [
        {
            "query": r["query"],
            "impressions": r["impressions"],
            "clicks": r["clicks"],
            "ctr": r["ctr"],
            "position": round(r["position"], 1),
        }
        for r in perf.get("by_query", [])
        if NEAR_PAGE1_MIN_POS <= r["position"] < NEAR_PAGE1_MAX_POS
        and r["impressions"] >= NEAR_PAGE1_MIN_IMP
    ]
    near.sort(key=lambda x: -x["impressions"])

    # LOW CTR on page 1: get clicks-per-impression too low for their position
    low_ctr = [
        {
            "query": r["query"],
            "impressions": r["impressions"],
            "clicks": r["clicks"],
            "ctr": r["ctr"],
            "position": round(r["position"], 1),
        }
        for r in perf.get("by_query", [])
        if r["position"] <= LOW_CTR_MAX_POS
        and r["impressions"] >= LOW_CTR_MIN_IMP
        and r["ctr"] < LOW_CTR_MAX_CTR
    ]
    low_ctr.sort(key=lambda x: -x["impressions"])

    # LOSING PAGES: pages whose clicks dropped meaningfully
    prior_map = {r["page"]: r for r in perf.get("prior_window", {}).get("by_page", [])}
    recent_map = {r["page"]: r for r in perf.get("recent_window", {}).get("by_page", [])}
    losing = []
    for page, prior in prior_map.items():
        if prior["clicks"] < LOSING_MIN_PRIOR_CLICKS:
            continue
        recent = recent_map.get(page, {"clicks": 0, "impressions": 0, "position": 0})
        delta = recent["clicks"] - prior["clicks"]
        pct = (delta / prior["clicks"]) * 100 if prior["clicks"] else 0
        if pct <= -LOSING_DECLINE_PCT:
            losing.append({
                "page": page,
                "clicks_prior": prior["clicks"],
                "clicks_recent": recent["clicks"],
                "delta": delta,
                "delta_pct": round(pct, 1),
                "position_recent": round(recent.get("position", 0), 1),
                "position_prior": round(prior.get("position", 0), 1),
            })
    losing.sort(key=lambda x: x["delta"])  # most-negative first

    payload = {
        "site": perf.get("site"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "period_days": perf.get("period_days"),
        "windows": {
            "recent": perf.get("recent_window", {}).get("start", "") + " to "
                    + perf.get("recent_window", {}).get("end", ""),
            "prior":  perf.get("prior_window", {}).get("start", "") + " to "
                    + perf.get("prior_window", {}).get("end", ""),
        },
        "thresholds": {
            "near_page_1": {
                "position_range": [NEAR_PAGE1_MIN_POS, NEAR_PAGE1_MAX_POS],
                "min_impressions": NEAR_PAGE1_MIN_IMP,
            },
            "low_ctr": {
                "max_position": LOW_CTR_MAX_POS,
                "min_impressions": LOW_CTR_MIN_IMP,
                "max_ctr": LOW_CTR_MAX_CTR,
            },
            "losing_pages": {
                "min_prior_clicks": LOSING_MIN_PRIOR_CLICKS,
                "decline_pct_threshold": LOSING_DECLINE_PCT,
            },
        },
        "near_page_1": near,
        "low_ctr": low_ctr,
        "losing_pages": losing,
    }

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (HERE / f"opportunities-{today}.json").write_text(json.dumps(payload, indent=2))
    (HERE / "opportunities-latest.json").write_text(json.dumps(payload, separators=(",", ":")))
    print(f"[opps] near_page_1: {len(near)}  low_ctr: {len(low_ctr)}  losing_pages: {len(losing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
