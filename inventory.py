"""
GSC coverage inventory for releasedsolutions.net.

For every URL declared in the sitemap, hit the Search Console URL Inspection API
and record: verdict, coverage state, canonical match, robots/indexing state,
last crawl time. Output both CSV (for spreadsheet analysis) and JSON (for the
artifact builder).

Usage:   python3 inventory.py
"""
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

KEY = "/Users/kennethdurrum/.config/seminole/gsc-service-account.json"
SITE = "sc-domain:releasedsolutions.net"
SITEMAP = "https://releasedsolutions.net/sitemap.xml"


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
        r = requests.get(u, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (rs-gsc-inventory; +kdurrum@releasedsolutions.net)",
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


def bucket_for(coverage: str) -> str:
    if not coverage:
        return "Unknown"
    return COVERAGE_BUCKET.get(coverage, coverage)


def main() -> int:
    creds = service_account.Credentials.from_service_account_file(
        KEY, scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
    sc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    print(f"[inventory] fetching sitemap {SITEMAP}")
    urls = sitemap_urls(SITEMAP)
    print(f"[inventory] {len(urls)} URLs to inspect")

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
        })
        print(f"  [{i:3d}/{len(urls)}] {bucket_for(coverage):15s} {u}")
        time.sleep(0.15)  # be a good API citizen

    # Write CSV
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    csv_path = f"inventory-{today}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"[inventory] wrote {csv_path}")

    # Write JSON (for artifact builder)
    json_path = f"inventory-{today}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "site": SITE,
            "sitemap": SITEMAP,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": len(rows),
            "rows": rows,
        }, f, indent=2)
    print(f"[inventory] wrote {json_path}")

    # Quick summary
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1
    print("\n=== bucket summary ===")
    for bucket, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {n:3d}  {bucket}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
