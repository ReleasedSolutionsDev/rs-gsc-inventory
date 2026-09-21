# GSC Coverage Inventory — System Blueprint

A two-stage daily pipeline that gives every website a live, sortable, per-URL Google indexing status board with historical tracking and per-user checkoff state — with zero local-machine involvement after setup.

**Reference implementation:** [ReleasedSolutionsDev/rs-gsc-inventory](https://github.com/ReleasedSolutionsDev/rs-gsc-inventory)
**Visual view:** [Blueprint artifact](https://claude.ai/artifact/Xok7Kw3L37cG6siLp1dS9m) *(owner-only; use this markdown for hand-offs to other operators)*

---

## TL;DR

Every morning, a scheduled job asks Google Search Console for the current coverage state of every URL in a site's sitemap — Indexed / Discovered-not-indexed / Crawled-not-indexed / Not-Discovered — then publishes a live web page (a Claude artifact) with a filterable table, a trend chart, and per-viewer checkboxes for tracking which pages you've asked Google to re-crawl.

**What makes it non-obvious:** the fetch has to happen on one cloud (GitHub Actions, which has the internet) but the publish has to happen on another (an Anthropic scheduled routine, which is the only thing that can call the Artifact tool). Splitting the work between the two — and knowing which layer blocks what — is the entire trick.

---

## 1. Architecture

```
   [Google URL Inspection]                         [Anthropic routine]
             │                                        cron 10:30 UTC
             ▼                                              │
   [GitHub Actions cron]  ──►  [Public GitHub repo]  ──►  [Claude Artifact]
    cron 10:00 UTC              inventory.html + history        live URL
    fetches + builds            (source of truth)
```

Data flows left → right, once per day. The **split** is forced by two runtime facts:

- Anthropic's routine environment blocks outbound HTTP to arbitrary hosts (allowlist is Anthropic APIs, PyPI, npm, GitHub). It **cannot** fetch `sitemap.xml` or call the Google API.
- GitHub Actions can reach everything but has no way to publish to a Claude artifact — that endpoint is only reachable from a Claude session with the Artifact tool.

So GHA does the fetch + build; the routine does the publish. The repo is the handoff surface between them.

---

## 2. Component inventory

### Public GitHub repo (source of truth)

One repo per site. Public so the routine's environment can clone it without extra auth.

| File | Role |
|---|---|
| `refresh.py` | The fetch + build script |
| `template.html` | Artifact page with `__DATA__` placeholder |
| `requirements.txt` | google-api-python-client, requests |
| `.github/workflows/refresh.yml` | GHA cron + steps |
| `.gitignore` | Must whitelist `history.json` (see gotchas) |
| `history.json` | Appended daily; source for trend chart |
| `inventory.html` | Rebuilt daily; committed for routine to pick up |
| `inventory-YYYY-MM-DD.{csv,json}` | Dated raw snapshots (idempotent history source) |

### `refresh.py` (GHA-side)

Deterministic Python 3 script. Reads `GSC_KEY_PATH` for the service-account JSON. Outputs everything else. Idempotent — rebuilds `history.json` from all dated `inventory-*.json` files each run, so re-running or backfilling never desyncs the trend chart.

Bucket-mapping (GSC `coverageState` → dashboard buckets):

```python
COVERAGE_BUCKET = {
    "Submitted and indexed": "Indexed",
    "URL is unknown to Google": "Not Discovered",
    "Discovered - currently not indexed": "Discovered",
    "Crawled - currently not indexed": "Crawled",
    "Duplicate, Google chose different canonical": "Duplicate",
    "Excluded by 'noindex' tag": "Noindex",
    "Page with redirect": "Redirect",
    "Not found (404)": "404",
    "Soft 404": "Soft 404",
    "Blocked by robots.txt": "Blocked",
    # see repo for full map
}
```

### `template.html` (artifact source)

Self-contained page. Declares runtime capabilities `{db: {}, user: {}}` on publish; the routine preserves them on subsequent publishes by omitting the field.

- **Data embed:** single `<script id="inv-data" type="application/json">__DATA__</script>`. `refresh.py` replaces `__DATA__` with the day's compact JSON (rows + history).
- **Checkbox state:** persisted per-viewer at `data/users/<uid>/checkmarks` via the artifact `db` capability. Falls back to `localStorage` when db is unavailable.
- **Trend chart:** inline SVG (no library). Reads `data.history`. Draws one line per bucket.

### `.github/workflows/refresh.yml` (GHA-side)

```yaml
name: Daily GSC inventory refresh
on:
  schedule:
    - cron: '0 10 * * *'   # 10:00 UTC = 6 AM ET during DST
  workflow_dispatch: {}
permissions:
  contents: write   # so the commit step can push
jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11', cache: pip }
      - run: pip install --quiet -r requirements.txt
      - env: { GSC_KEY_JSON: "${{ secrets.GSC_KEY_JSON }}" }
        run: printf '%s' "$GSC_KEY_JSON" > /tmp/gsc.json && chmod 600 /tmp/gsc.json
      - env: { GSC_KEY_PATH: /tmp/gsc.json }
        run: python3 refresh.py
      - if: always()
        run: rm -f /tmp/gsc.json
      - run: |
          git config user.name  "gsc-inventory-bot"
          git config user.email "actions@github.com"
          git add inventory.html bucket-summary.txt history.json inventory-*.csv inventory-*.json 2>/dev/null || true
          git diff --cached --quiet && exit 0
          git commit -m "chore: daily inventory refresh $(date -u +%Y-%m-%d)"
          git push
```

### Anthropic scheduled routine (publish-side)

Created via the RemoteTrigger API. Fires 30 minutes after the GHA cron. Prompt is intentionally credential-free — it only publishes what GHA already built.

```
STEP 1 — Confirm the checkout has the freshly-built files.
Run: ls -la inventory.html bucket-summary.txt

STEP 2 — Read bucket-summary.txt.

STEP 3 — Publish inventory.html to the existing artifact.
Use the Artifact tool with EXACTLY these parameters:
  file_path: inventory.html
  url: https://claude.ai/artifact/<ARTIFACT_ID>
Do NOT pass 'capabilities', 'title', 'favicon', or 'icon' —
omitting them preserves the artifact's existing db + user
capability declaration and title.

STEP 4 — Print the summary line + artifact URL. Nothing else.
```

Config:
- `model: claude-sonnet-5`
- `allowed_tools: [Bash, Read, Write, Edit, Glob, Grep, Artifact]`
- `environment_id: env_01TVUi7rPuyYVJCvM6ZQZW58` (Anthropic default cloud)
- `sources: [{ git_repository: { url: <public-repo> } }]`
- `cron_expression: 30 10 * * *`

### Claude Artifact (delivered page)

Owned by the operator. One per site. Fixed URL that never changes across daily republishes. Capabilities `db + user` declared once at first publish — the routine preserves them by omitting `capabilities` on every subsequent publish.

---

## 3. Setup checklist for a new site

1. **Create (or reuse) a Google service account** with `webmasters.readonly` scope. One SA can service many properties.
2. **Add the service account email to Google Search Console** as a delegated user with "Full" permission on the target property.
3. **Create a public GitHub repo** named `<site-slug>-gsc-inventory`. Public is required — the routine env cannot clone private repos without extra auth. Nothing sensitive lives in the repo; the GSC key stays in GHA secrets only.
4. **Copy six files** from the reference implementation: `refresh.py`, `template.html`, `requirements.txt`, `.github/workflows/refresh.yml`, `.gitignore`, `README.md`.
5. **Edit `refresh.py` for the new site.** Set `SITE = "sc-domain:example.com"` and `SITEMAP = "https://example.com/sitemap.xml"`. Nothing else changes.
6. **Add the GHA repo secret `GSC_KEY_JSON`.**
   `gh secret set GSC_KEY_JSON -R <org>/<repo> < path/to/key.json`
7. **If the site is on Cloudflare, verify Bot Fight Mode is OFF.** Dashboard → Security → Bots. It blocks GHA runner IPs. Alternative: add a Configuration Rule setting `security_level: essentially_off` for `/sitemap.xml`, `/sitemap-*.xml`, `/robots.txt`.
8. **If the site has country-block WAF rules, add an exemption.** Patch the country-block rule's expression to end with `and not (http.request.uri.path in {"/sitemap.xml" "/sitemap-index.xml" "/robots.txt"})`.
9. **Fire the workflow manually to verify end-to-end.**
   `gh workflow run refresh.yml -R <org>/<repo>`
   Should succeed and commit `inventory.html`, `history.json`, dated CSV + JSON, `bucket-summary.txt`.
10. **Publish the artifact once, manually, with capabilities declared.** From a Claude Code session in the repo directory: publish `inventory.html` with `capabilities: {db: {}, user: {}}` and a favicon. Copy the resulting `claude.ai/artifact/...` URL.
11. **Create the Anthropic scheduled routine via RemoteTrigger.** Cron `30 10 * * *`. Sources: the newly created repo. Prompt: the shape above with the artifact URL substituted in.
12. **Fire the routine once via `RemoteTrigger.run`** to verify publishing. Watch `list_runs` + `get_run_log`. First run reads the live artifact, then publishes successfully.
13. **Save a memory / doc entry** linking site → repo → artifact ID → routine trigger ID. Reference field for future audits or key rotations.

---

## 4. Gotchas — every rake we stepped on

### Blockers (system does not work until fixed)

**Cloudflare Bot Fight Mode blocks GHA runner IPs.** Bot Fight Mode issues managed-challenge responses to any request from known cloud/hosting ranges. Custom firewall rules with `action: skip` do **not** override it — it's a separate zone-level toggle. Fix: turn Bot Fight Mode off in Security → Bots, or add a Cloudflare Configuration Rule for the sitemap paths setting `security_level: essentially_off`.

**Anthropic routine environment cannot fetch arbitrary hosts.** The env's egress proxy allowlist covers Anthropic APIs, PyPI, npm, GitHub. Not the target site, not Google APIs. Attempting `refresh.py` inside the routine fails with `403 Forbidden` on CONNECT. Fix: split the pipeline — fetch in GHA, publish in the routine.

**Routine's Claude session refuses "mismatched-looking" credentials.** If the routine prompt includes a service-account key whose email doesn't match the target domain (e.g. `seminole-service-account@…` operating on `releasedsolutions.net`), the running Claude session flags it as a possible credential mix-up and halts before using it. Right response: don't put credentials in the routine prompt at all — split the pipeline so the routine only publishes what GHA already built.

### Gotchas (surprising, cost time to discover)

**Country-block WAF rules also fire on cloud runner IPs.** A rule like `not ip.geoip.country in {"US" "CA" "GB"}` blocks a GHA runner if it lands in a non-listed geo (Azure runners run globally). Fix: patch the rule to exempt public assets — sitemaps and robots.txt should always be reachable.

**GHA needs the `workflow` scope to push a new workflow file.** The default `repo` scope on `gh auth` tokens cannot push to `.github/workflows/*`. Fix: `gh auth refresh -h github.com -s workflow` once, then push.

**`.gitignore`'s `*.json` silently drops `history.json`.** A common pattern is to ignore `*.json` and whitelist `!inventory-*.json`. That still ignores `history.json` — the `git add` in the workflow doesn't error, it just skips it. Fix: also add `!history.json`.

### By design (know so you don't fight them)

**Artifact publish refuses if the session hasn't Read the current version.** First publish attempt in a new session returns "You hadn't viewed the live version". This is deliberate — it prevents overwriting concurrent edits. The routine handles it automatically: Read the live URL, then publish again. If your local file is a superset of the live version (adds new UI), publish the local file directly — the platform accepts it since the diff is intentional.

**Declaring `db` or `assets` capabilities makes the artifact organization-internal.** Public link sharing is disabled on artifacts that declare `db`. Only signed-in members of the owner's Anthropic organization can open it. That's the platform's policy — no workaround. If you need a truly public dashboard, don't declare `db` and give up per-viewer state.

### Limits

**GSC URL Inspection API rate limit.** 2,000 inspections per property per day; 600 per minute. For a site up to ~1,800 URLs, one daily full-scan fits comfortably. Larger sites need incremental batching (e.g. inspect 500 URLs/day on rotation).

**Fetching sitemap with default `requests` user-agent returns 406 on some hosts.** Cloudflare + BlueHost sometimes 406 the default Python `requests` UA. Set a browser UA in `refresh.py`. Doesn't fix Bot Fight Mode; only fixes the vanilla 406.

---

## 5. Multi-site scaling

Two shapes, pick based on how many sites you're managing:

**Per-site repo (≤ ~5 sites).** Each site gets its own repo, its own workflow, its own artifact, its own routine. Highest isolation. Cleanest blast radius if one site's config drifts.

**Monorepo (≥ ~5 sites).** One `gsc-inventory` repo with a `sites/` directory. Each site is a small YAML config: sitemap URL, GSC property ID, artifact URL, service-account secret name. The workflow becomes a matrix build (one job per site).

Trade-offs:
- Monorepo: one credential store, easier central updates, harder per-site scoping.
- Per-site: cleaner isolation, more secrets to rotate, more repos to update when `template.html` changes.

---

## 6. Cost and limits

| Resource | Per site per day | Practical ceiling |
|---|---:|---|
| GHA compute | 1–2 min | 2000 free min/mo → ~30 sites |
| GSC URL Inspection API | = sitemap size | 2000/property/day |
| Anthropic routine tokens | ~15–25k in / 1–2k out | ~$0.05–0.15 per site per day |
| Repo storage growth | ~150 KB (CSV + JSON) | 50 MB/year — trim old dated files annually |
| Artifact storage | 1 version | — |

At **10 sites**: roughly **$0.50–$1.50/day** in Anthropic routine tokens; everything else effectively free.

---

## 7. Operations

### Rotating a GSC service account key

1. Generate a new key in Google Cloud Console for the service account.
2. `gh secret set GSC_KEY_JSON -R <org>/<repo> < path/to/new-key.json`
3. Delete the old key in Google Cloud Console.
4. Fire the workflow once to confirm it still runs.

### Adding new URLs to an existing site

Nothing to do. The workflow reads the sitemap fresh on every run, so any URL published to the site's sitemap gets picked up on the next daily scan.

### When Google's coverage report and this system disagree

The URL Inspection API is real-time; the GSC UI's coverage report can lag by days. Trust the API. If a URL shows Indexed here but "not indexed" in the UI, the UI just hasn't refreshed yet.

### If a run fails silently

- **GHA:** `gh run list -R <org>/<repo>` — most failures show `completed / failure` with logs.
- **Routine:** `RemoteTrigger action=list_runs trigger_id=<id>` → then `get_run_log session_id=<id>` for the transcript.

---

## 8. References

- [Reference implementation repo](https://github.com/ReleasedSolutionsDev/rs-gsc-inventory)
- [GSC URL Inspection API](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect)
- [GitHub Actions docs](https://docs.github.com/en/actions)
- [Cloudflare Custom Rules](https://developers.cloudflare.com/waf/custom-rules/)

---

Blueprint drafted from a working reference implementation. Every gotcha above was hit during construction; the fixes are the ones that actually worked.
