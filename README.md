# Insider Ledger

A static, self-hosted "morning read" dashboard of S&P 500 Form 4 (and 4/A)
insider transactions, pulled directly from SEC EDGAR. No backend server —
a scheduled GitHub Action runs the pipeline, writes `docs/data/report.json`,
and GitHub Pages (or Cloudflare Pages) serves the static site.

**Status: built and unit-tested against a realistic Form 4 fixture, but
never run against live EDGAR data or deployed.** See "What's verified vs.
not" below before you rely on this instead of your existing tool.

## What's verified vs. not

| Piece | Status |
|---|---|
| XML parsing logic (P/S codes, $10K floor, ownership, role, dates) | ✅ Unit-tested against a realistic fixture (`tests/`) |
| Signal tagging + cluster detection + aggregation | ✅ Unit-tested end to end |
| DST-aware schedule gate (dual cron + local-hour check) | ✅ Logic-tested for both EDT and EST dates |
| Dashboard UI (toggles, sort, column show/hide, empty states) | ✅ Same design as your reference site, adapted to fetch real JSON |
| **Live EDGAR requests** (browse-edgar feed, index.json, primary XML) | ❌ **Never executed** — written to the documented API contracts, not exercised against a live response |
| **10b5-1 schema field** | ⚠️ SEC moved this around across schema revisions; the code checks a few candidate tag names plus a footnote-text fallback. Treat the dedicated-field check as unverified until you see it hit a real 2023+ filing; the footnote fallback is the reliable part. |
| **GitHub Actions run (scheduled or manual)** | ❌ **Never executed** — I don't have push access to a repo or a GitHub connector in this session |
| **GitHub Pages / Cloudflare Pages deploy** | ❌ **Never executed** — same reason |
| **S&P 500 constituent list** | ⚠️ `config/sp500_constituents.sample.csv` has 12 companies as a bootstrap. Run `scripts/build_constituents.py` to generate the real 500-row list before trusting coverage. |

The gap above is structural, not laziness: this environment has no network
access from its code sandbox and no GitHub/Cloudflare connector, so nothing
that requires the public internet or a real repo could be executed here.
Everything that *could* be tested without those things, was.

## How this differs from the old ChatGPT-hosted version

That version had a real backend already running somewhere (its own cron +
server). This version has no server at all: GitHub Actions runs the Python
script on a schedule, commits the resulting JSON into the repo, and GitHub
Pages/Cloudflare Pages serves that JSON as a static file. The browser never
talks to SEC EDGAR — only the Action does.

## Setup (10–15 minutes)

1. **Create a new GitHub repo** and push this folder's contents to it.
   ```
   cd insider-ledger
   git init
   git add -A
   git commit -m "Initial Insider Ledger pipeline"
   git branch -M main
   git remote add origin https://github.com/<you>/insider-ledger.git
   git push -u origin main
   ```

2. **Add the SEC User-Agent secret.** SEC requires an identifying User-Agent
   on every request (their fair-access policy — see
   https://www.sec.gov/os/webmaster-faq#developers). In your repo:
   `Settings → Secrets and variables → Actions → New repository secret`
   - Name: `SEC_USER_AGENT`
   - Value: `InsiderLedger/1.0 (your-real-email@example.com)` — use a real
     contact address; SEC does rate-limit or block generic/anonymous agents.

3. **Build the real constituent list** (replaces the 12-row sample):
   ```
   pip install -r requirements.txt
   python scripts/build_constituents.py
   git add config/sp500_constituents.csv
   git commit -m "Populate full S&P 500 constituent list"
   git push
   ```
   Re-run this quarterly, or after any S&P 500 rebalance announcement.

4. **Enable GitHub Pages.** `Settings → Pages → Source: Deploy from a branch
   → Branch: main, folder: /docs → Save`. GitHub will give you a URL like
   `https://<you>.github.io/insider-ledger/`. That's your public dashboard link.

   *Alternative: Cloudflare Pages.* Connect the repo in the Cloudflare
   dashboard, set build output directory to `docs`, no build command needed
   (it's static HTML/JS). Cloudflare will give you a `*.pages.dev` URL, or
   attach a custom domain.

5. **Test it manually before trusting the schedule.** In your repo:
   `Actions → Morning Insider Report → Run workflow`. This is requirement
   #17 from the spec — `workflow_dispatch` always runs regardless of time
   of day, specifically so you can test on demand. Check the run logs; if
   it fails, see Troubleshooting below.

6. **Confirm the schedule fires correctly** by watching the Actions tab
   around 8:00 AM ET on a weekday (or just trust the logic-test above —
   both cron entries are gated by an explicit `TZ=America/New_York date +%H`
   check, so only the one that actually corresponds to 8 AM local time will
   proceed; the other exits immediately as a no-op).

Only once you've seen a manual run succeed and produce a sensible
`docs/data/report.json` should you consider retiring the old version.

## Repo layout

```
config/sp500_constituents.csv        # ticker,cik,company,sector,industry (generate via build_constituents.py)
config/sp500_constituents.sample.csv # 12-row bootstrap sample, checked in as a fallback
scripts/edgar_client.py              # rate-limited, User-Agent-aware HTTP client
scripts/parse_form4.py               # ownershipDocument XML -> Transaction records
scripts/signals.py                   # signal tags + cluster-activity detection
scripts/build_report.py              # aggregation, trailing 4-week averages, report.json shape
scripts/fetch_report.py              # orchestrator / entry point
scripts/build_constituents.py        # rebuilds the real S&P 500 + CIK list (run locally, needs network)
docs/index.html                      # the dashboard (fetches docs/data/report.json)
docs/data/report.json                # latest snapshot — this is what the pipeline overwrites
docs/data/history/*.json             # one snapshot per day, used to compute trailing 4-week averages
tests/                               # network-free unit tests
.github/workflows/morning-report.yml # scheduled + manual pipeline run
```

## How the pipeline works

1. For each ticker/CIK in `config/sp500_constituents.csv`, hit EDGAR's company
   browse feed filtered to Form 4 (`action=getcompany&type=4&output=atom`).
   This is indexed by issuer even though insiders are the actual filers —
   it's the same mechanism SEC's own web UI uses to show "insider
   transactions for company X."
2. Keep only filings dated the current or previous calendar day (America/New_York).
3. For each matching filing, fetch its `index.json` to find the primary XML
   document, then fetch and parse that XML for non-derivative transactions
   coded `P` or `S`, dropping anything under $10,000.
4. Tag each transaction with signals (role weight, direct/indirect,
   purchase vs. sale, value tier, cluster activity, 10b5-1, amended) via
   `signals.py`.
5. Aggregate into `report.json`: stats, the Morning Read paragraph, highest
   signal, trailing 4-week averages (computed from `docs/data/history/`),
   data health, and the full transaction list.
6. Always write the file — an empty purchases list still produces a valid
   report; the dashboard shows "No qualifying purchases" instead of falling
   back to an all-sales view.

## Changing the schedule

Edit `.github/workflows/morning-report.yml`:
- To change the time: update both cron lines (remember they're UTC) and the
  `hour=$(TZ="America/New_York" date +%H)` comparison in the gate step to
  your new target hour.
- To change the days: adjust `1-5` (Mon–Fri) in both cron expressions.
- To add a second daily run (e.g. a midday check): add another pair of
  cron lines (one per DST offset) with their own hour comparison, or
  duplicate the job with a different gate hour.

## Troubleshooting

- **`RuntimeError: SEC_USER_AGENT is not set`** — add the repo secret
  (step 2 above) or export it locally: `export SEC_USER_AGENT="..."`.
- **Workflow runs but commits nothing** — no qualifying transactions
  changed since last run; `git diff --quiet --cached` short-circuits the
  commit. Check the run's printed summary line for actual counts.
- **429 / rate-limit errors from SEC** — lower `min_interval` isn't the
  fix; *raise* it in `edgar_client.py` (e.g. `0.25`), and confirm your
  `SEC_USER_AGENT` looks like a real identifying string, not a generic
  library default.
- **Dashboard shows "Data unavailable"** — `docs/data/report.json` is
  missing or malformed for the deployed branch/folder. Confirm Pages is
  serving from `main` / `/docs`, and that the workflow's commit step
  actually ran (check the Actions log).
- **Trailing 4-week averages look off right after setup** — they're
  computed from `docs/data/history/`, which starts empty. Averages will
  read low/zero until ~20 weekday snapshots accumulate (about 4 weeks in).
- **A specific company shows no data despite known insider activity** —
  check that ticker's CIK in `config/sp500_constituents.csv` is correct;
  a wrong or stale CIK silently returns zero filings rather than erroring.

## Known follow-ups (read before your first real run)

- The 10b5-1 dedicated-field detection in `parse_form4.py` checks a few
  candidate tag names that may not match SEC's actual current schema —
  verify against a handful of real 2023+ filings and adjust
  `_find_rule_10b5_1()` if needed. The footnote-text fallback is more
  reliable in the meantime.
- `scripts/build_constituents.py` scrapes Wikipedia's S&P 500 table, which
  is community-maintained and can lag a real rebalance by a few days.
  Fine for a daily insider-activity monitor; not a substitute for a paid
  index-membership feed if perfect real-time accuracy matters to you.
- Consider adding a second workflow step that fails loudly (e.g. opens a
  GitHub issue) if `errors` in `report.json` crosses some threshold, so a
  silently degrading feed doesn't go unnoticed.
