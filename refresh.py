"""
Refresh flow: run inventory + inject fresh data into template.html → inventory.html.

Reads GSC service account key from GSC_KEY_PATH (default: ./gsc-service-account.json,
or fall back to ~/.config/seminole/gsc-service-account.json).

Emits three files in the working directory:
    inventory-YYYY-MM-DD.csv      raw table for spreadsheet analysis
    inventory-YYYY-MM-DD.json     raw data for scripting
    inventory.html                the artifact HTML with embedded fresh data

Next step (in a Claude Code session): publish inventory.html to the artifact URL
    https://claude.ai/artifact/QUA2ou6G24VVrgC1SE6Ni6
"""
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


HERE = Path(__file__).resolve().parent
SITE = "sc-domain:releasedsolutions.net"
SITEMAP = "https://releasedsolutions.net/sitemap.xml"
TEMPLATE = HERE / "template.html"


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


def sitemap_urls(root: str) -> list[str]:
    seen: set[str] = set()
    pending = [root]
    urls: list[str] = []
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    while pending:
        u = pending.pop()
        if u in seen:
            continue
        seen.add(u)
        # Browser-style UA — Cloudflare on releasedsolutions.net 403s custom UAs
        r = requests.get(u, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "application/xml, text/xml, */*",
        })
        r.raise_for_status()
        try:
            root_el = ET.fromstring(r.text)
        except ET.ParseError as e:
            print(f"[warn] can't parse {u}: {e}", file=sys.stderr)
            continue
        for loc in root_el.findall(".//sm:sitemap/sm:loc", ns):
            pending.append((loc.text or "").strip())
        for loc in root_el.findall(".//sm:url/sm:loc", ns):
            urls.append((loc.text or "").strip())
    return sorted(set(urls))


COVERAGE_BUCKET = {
    "Submitted and indexed": "Indexed",
    "Indexed, not submitted in sitemap": "Indexed",
    "URL is unknown to Google": "Not Discovered",
    "Discovered - currently not indexed": "Discovered",
    "Crawled - currently not indexed": "Crawled",
    "Duplicate without user-selected canonical": "Duplicate",
    "Duplicate, Google chose different canonical than user": "Duplicate",
    "Alternate page with proper canonical tag": "Alternate",
    "Blocked by robots.txt": "Blocked",
    "Excluded by 'noindex' tag": "Noindex",
    "Page with redirect": "Redirect",
    "Not found (404)": "404",
    "Soft 404": "Soft 404",
    "Server error (5xx)": "Server error",
}

STALE_CANDIDATE_BUCKETS = {"404", "Soft 404"}


def bucket_for(coverage: str) -> str:
    if not coverage:
        return "Unknown"
    return COVERAGE_BUCKET.get(coverage, coverage)


BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def live_status(url: str) -> int:
    """HEAD-check a URL, follow redirects, return final status code. 0 on error."""
    try:
        r = requests.head(url, timeout=10, allow_redirects=True, headers={
            "User-Agent": BROWSER_UA,
            "Accept": "*/*",
        })
        return r.status_code
    except Exception:
        return 0


def main() -> int:
    creds = service_account.Credentials.from_service_account_file(
        key_path(), scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
    sc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    urls = sitemap_urls(SITEMAP)
    print(f"[refresh] {len(urls)} URLs to inspect")

    rows: list[dict] = []
    for i, u in enumerate(urls, 1):
        try:
            r = sc.urlInspection().index().inspect(body={
                "inspectionUrl": u, "siteUrl": SITE
            }).execute()
        except HttpError as e:
            print(f"  [{i:3d}/{len(urls)}] ERROR  {u} :: {e}")
            rows.append({
                "url": u, "verdict": "ERROR", "coverage": str(e)[:80],
                "bucket": "Error", "robots": "", "indexing": "",
                "user_canonical": "", "google_canonical": "",
                "canonical_match": "", "last_crawl": "", "fetch": "",
            })
            continue

        idx = r.get("inspectionResult", {}).get("indexStatusResult", {}) or {}
        verdict = idx.get("verdict") or ""
        coverage = idx.get("coverageState") or ""
        robots = idx.get("robotsTxtState") or ""
        indexing = idx.get("indexingState") or ""
        user_canon = idx.get("userCanonical") or ""
        google_canon = idx.get("googleCanonical") or ""
        canon_match = "match" if user_canon == google_canon and user_canon else \
                      ("MISMATCH" if user_canon and google_canon else "")
        last_crawl = idx.get("lastCrawlTime") or ""
        fetch_state = idx.get("pageFetchState") or ""

        rows.append({
            "url": u,
            "verdict": verdict,
            "coverage": coverage,
            "bucket": bucket_for(coverage),
            "robots": robots,
            "indexing": indexing,
            "user_canonical": user_canon,
            "google_canonical": google_canon,
            "canonical_match": canon_match,
            "last_crawl": last_crawl,
            "fetch": fetch_state,
            "live_status": 0,
        })
        print(f"  [{i:3d}/{len(urls)}] {bucket_for(coverage):15s} {u}")
        time.sleep(0.15)

    # --- Live re-verify: 404/Soft 404 in the API can be stale.
    # HEAD-check each and, if the live URL now serves 200, reclassify as "Stale".
    for row in rows:
        if row["bucket"] not in STALE_CANDIDATE_BUCKETS:
            continue
        code = live_status(row["url"])
        row["live_status"] = code
        if 200 <= code < 400 and code != 0:
            print(f"  [stale] {row['bucket']:9s} → Stale · live={code} · {row['url']}")
            row["bucket"] = "Stale"
        else:
            print(f"  [confirmed 404] live={code} · {row['url']}")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # CSV (raw)
    csv_path = HERE / f"inventory-{today}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"[refresh] wrote {csv_path.name}")

    # JSON (raw)
    payload = {
        "site": SITE,
        "sitemap": SITEMAP,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(rows),
        "rows": rows,
    }
    json_path = HERE / f"inventory-{today}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"[refresh] wrote {json_path.name}")

    # HTML for artifact publish — inject compact JSON into template
    if not TEMPLATE.exists():
        print(f"ERROR: {TEMPLATE} missing", file=sys.stderr)
        return 1

    # Compact version used by the artifact (same shape, minified)
    embed = {
        "generated_utc": payload["generated_utc"],
        "total": payload["total"],
        "rows": [
            {k: r[k] for k in (
                "url", "bucket", "coverage", "robots", "indexing",
                "last_crawl", "canonical_match", "user_canonical", "google_canonical",
                "live_status",
            )} for r in rows
        ],
    }
    # match the artifact's field names (canon_match not canonical_match)
    for r in embed["rows"]:
        r["canon_match"] = r.pop("canonical_match")

    # --- History: rebuild from all dated JSONs so this is idempotent ---
    from datetime import datetime as _dt
    history: list[dict] = []
    seen_dates: set[str] = set()
    # Prefer today's counts computed above; scan siblings for older days
    today_counts = {}
    for row in rows:
        today_counts[row["bucket"]] = today_counts.get(row["bucket"], 0) + 1
    history.append({"date": today, "counts": today_counts})
    seen_dates.add(today)
    for jp in sorted(HERE.glob("inventory-*.json")):
        try:
            date = jp.stem.replace("inventory-", "")
            _dt.strptime(date, "%Y-%m-%d")  # validate
        except Exception:
            continue
        if date in seen_dates:
            continue
        try:
            d = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        c: dict[str, int] = {}
        for r in d.get("rows") or []:
            b = r.get("bucket") or "Unknown"
            c[b] = c.get(b, 0) + 1
        history.append({"date": date, "counts": c})
        seen_dates.add(date)
    history.sort(key=lambda e: e["date"])

    (HERE / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"[refresh] wrote history.json ({len(history)} day(s))")

    # Embed history in the artifact's data block so the routine's merge picks it up
    embed["history"] = history

    data_safe = json.dumps(embed, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data_safe, 1)
    (HERE / "inventory.html").write_text(html, encoding="utf-8")
    print(f"[refresh] wrote inventory.html ({len(html)} bytes)")

    # Summary
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1
    ordered = ["Indexed", "Not Discovered", "Discovered", "Crawled"]
    parts = [f"{counts[b]} {b}" for b in ordered if counts.get(b)]
    parts += [f"{n} {b}" for b, n in counts.items() if b not in ordered]
    summary = " · ".join(parts)
    (HERE / "bucket-summary.txt").write_text(summary + "\n", encoding="utf-8")

    print("\n== bucket summary ==")
    for bucket, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {n:3d}  {bucket}")
    print(f"\n{summary}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
