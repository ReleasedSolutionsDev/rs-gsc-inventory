# rs-gsc-inventory

Daily URL Inspection for every URL in `releasedsolutions.net/sitemap.xml` → rebuilds the coverage-inventory artifact so the "which pages still need indexing" view stays current without any local machine running.

**Artifact URL:** https://claude.ai/artifact/QUA2ou6G24VVrgC1SE6Ni6

## Files

| File | Purpose |
|---|---|
| `refresh.py` | Main flow. Fetches sitemap, calls GSC URL Inspection per URL, writes CSV + JSON, injects fresh data into `template.html` → `inventory.html`. |
| `template.html` | Artifact HTML with `__DATA__` placeholder. Rewrite this if you want to change the artifact's layout — never edit `inventory.html` directly. |
| `inventory.py` | Older stand-alone inventory (CSV/JSON only). Kept for one-off manual scans. |
| `requirements.txt` | google-auth, google-api-python-client, requests. |

## Auth

`refresh.py` looks for the Search Console service account JSON in this order:

1. `$GSC_KEY_PATH` env var
2. `./gsc-service-account.json`
3. `~/.config/seminole/gsc-service-account.json`

The service account is `seminole-service-account@seminoletheatre.iam.gserviceaccount.com`, added as Full user on the `sc-domain:releasedsolutions.net` GSC property.

## Manual run (from any Mac)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 refresh.py
# then publish inventory.html to the artifact URL — inside a Claude Code session:
#   Use the Artifact tool with url=https://claude.ai/artifact/QUA2ou6G24VVrgC1SE6Ni6
```

## Daily cloud refresh (two-stage, no local machine involved)

1. **GitHub Actions** — `.github/workflows/refresh.yml` fires daily at 10:00 UTC (6 AM ET during DST). It runs `refresh.py`, using the `GSC_KEY_JSON` repo secret, then commits the fresh `inventory.html` and `bucket-summary.txt` back to `main`.
2. **Anthropic scheduled routine** `trig_01KaNQzkb6P76T7r3XM1bxpg` — fires 30 min later at 10:30 UTC. Clones the freshly-updated repo and publishes `inventory.html` to the Claude artifact via the Artifact tool.

Why the split: Anthropic's routine environment has an outbound-egress proxy that only allows Anthropic APIs, PyPI, npm, and GitHub — it can't reach `releasedsolutions.net` or `*.googleapis.com`. GitHub Actions has no such restriction, so it does the fetch. The routine handles only the artifact publish (which uses Anthropic's own API and is allowed).

**GSC key lives only in `GSC_KEY_JSON` GitHub Actions secret** — not in the routine prompt or the repo. Rotate via GCP console then update the secret with `gh secret set GSC_KEY_JSON -R ReleasedSolutionsDev/rs-gsc-inventory < path/to/new-key.json`.
