"""Pull Google Search Console Search Analytics data.

Emits perf-YYYY-MM-DD.json with clicks/impressions/CTR/position broken down
by query and by page over the trailing 90 days. Uses the same service account
key refresh.py uses (GSC_KEY_PATH env or default paths).

GSC has a 2-3 day data delay, so "last 90 days" really means 90 days ending
3 days ago. That is standard and matches Search Console UI behavior.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


HERE = Path(__file__).resolve().parent
SITE = "sc-domain:releasedsolutions.net"
PERIOD_DAYS = 90
ROW_LIMIT = 25000  # GSC hard cap per query


def key_path() -> str:
    env = os.environ.get("GSC_KEY_PATH")
    if env and Path(env).exists():
        return env
    local = HERE / "gsc-service-account.json"
    if local.exists():
        return str(local)
    fallback = Path.home() / ".config" / "seminole" / "gsc-service-account.json"
    if fallback.exists():
        return str(fallback)
    print("ERROR: no GSC key found. Set GSC_KEY_PATH.", file=sys.stderr)
    sys.exit(2)


def query_gsc(sc, start: str, end: str, dimensions: list[str]) -> list[dict]:
    """Paginate a Search Analytics query. Returns list of rows."""
    all_rows: list[dict] = []
    start_row = 0
    while True:
        req = {
            "startDate": start,
            "endDate": end,
            "dimensions": dimensions,
            "rowLimit": ROW_LIMIT,
            "startRow": start_row,
            "dataState": "final",
        }
        try:
            resp = sc.searchanalytics().query(siteUrl=SITE, body=req).execute()
        except HttpError as e:
            print(f"[perf] HttpError paginated at startRow={start_row}: {e}", file=sys.stderr)
            break
        rows = resp.get("rows", []) or []
        for r in rows:
            keys = r.get("keys", [])
            all_rows.append({
                **{dim: keys[i] for i, dim in enumerate(dimensions)},
                "clicks": r.get("clicks", 0),
                "impressions": r.get("impressions", 0),
                "ctr": r.get("ctr", 0.0),
                "position": r.get("position", 0.0),
            })
        if len(rows) < ROW_LIMIT:
            break
        start_row += ROW_LIMIT
        time.sleep(0.2)
    return all_rows


def main() -> int:
    creds = service_account.Credentials.from_service_account_file(
        key_path(), scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
    sc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    today = datetime.now(timezone.utc).date()
    # GSC data lag: use "yesterday - PERIOD_DAYS" through "yesterday - 3"
    end = today - timedelta(days=3)
    start = end - timedelta(days=PERIOD_DAYS - 1)

    print(f"[perf] pulling {start} → {end} ({PERIOD_DAYS} days)")

    # Totals via date dimension (this is what UI actually matches)
    print("[perf] totals via date dimension...")
    date_rows = query_gsc(sc, str(start), str(end), ["date"])
    totals = {
        "clicks": sum(r["clicks"] for r in date_rows),
        "impressions": sum(r["impressions"] for r in date_rows),
    }
    totals["ctr"] = (totals["clicks"] / totals["impressions"]) if totals["impressions"] else 0.0
    avg_pos = sum(r["position"] * r["impressions"] for r in date_rows)
    totals["position"] = (avg_pos / totals["impressions"]) if totals["impressions"] else 0.0

    print(f"[perf] totals: {totals['clicks']} clicks, {totals['impressions']} impressions, "
          f"CTR {totals['ctr']*100:.2f}%, pos {totals['position']:.1f}")

    # Per-query
    print("[perf] by_query...")
    by_query = query_gsc(sc, str(start), str(end), ["query"])
    print(f"[perf] {len(by_query)} unique queries")

    # Per-page
    print("[perf] by_page...")
    by_page = query_gsc(sc, str(start), str(end), ["page"])
    print(f"[perf] {len(by_page)} unique pages")

    # Per-query + page (used for opportunity discovery)
    print("[perf] by_query_page...")
    by_query_page = query_gsc(sc, str(start), str(end), ["query", "page"])
    print(f"[perf] {len(by_query_page)} query/page pairs")

    # Also pull the last-14d and prior-14d slices for decay detection
    print("[perf] recent/prior windows for decay signal...")
    recent_end = end
    recent_start = end - timedelta(days=13)
    prior_end = recent_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=13)
    recent_pages = query_gsc(sc, str(recent_start), str(recent_end), ["page"])
    prior_pages = query_gsc(sc, str(prior_start), str(prior_end), ["page"])

    # Daily click series (for trend chart)
    daily = [{"date": r["date"], "clicks": r["clicks"], "impressions": r["impressions"]}
             for r in date_rows]
    daily.sort(key=lambda x: x["date"])

    payload = {
        "site": SITE,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "period_days": PERIOD_DAYS,
        "start_date": str(start),
        "end_date": str(end),
        "totals": totals,
        "daily": daily,
        "by_query": by_query,
        "by_page": by_page,
        "by_query_page": by_query_page,
        "recent_window": {"start": str(recent_start), "end": str(recent_end),
                          "by_page": recent_pages},
        "prior_window": {"start": str(prior_start), "end": str(prior_end),
                          "by_page": prior_pages},
    }
    out = HERE / f"perf-{today}.json"
    out.write_text(json.dumps(payload, indent=2))
    print(f"[perf] wrote {out.name} ({out.stat().st_size} bytes)")

    # Also write a stable pointer to the latest
    (HERE / "perf-latest.json").write_text(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
