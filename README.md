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

## Daily cloud refresh

A scheduled Anthropic-hosted routine runs `refresh.py` and re-publishes the artifact once a day. See `~/.claude/…/routines/` for the schedule definition (managed via the `schedule` skill).
