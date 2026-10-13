# Before You Sign

**A team of AI agents that checks a New Haven apartment, its landlord, and its lease before a renter signs.**

Type in an address (and, optionally, upload the lease). Four agents pull public records and read the lease. The **Landlord Investigator** finds the real owner, how many properties they hold, and the people behind the LLC. The **Neighborhood Scout** checks 311 complaints, eviction filings and the FEMA flood zone. The **Lease Reviewer** checks every clause against Connecticut tenant law. The **Lead Advisor** turns it all into a sourced, plain-English report with questions to ask the landlord.

MGT 409 (AI Foundations for Managers) final project.

---

## Run it (about 3 minutes)

You need **Python 3.10 or newer** and a `PORTKEY_API_KEY`. No other software, accounts or keys are needed. All the data sources are free public APIs.

**1. Clone the repo and go into it**

```bash
git clone https://github.com/madelineanh/before-you-sign.git
cd before-you-sign
```

**2. Put your key in a `.env` file**

The app looks for `.env` **in this folder first, then in the folder above it**. If your `.env` with `PORTKEY_API_KEY` already sits one level above the repo, skip this step. Otherwise:

```bash
cp .env.example .env
```

Then open `.env` and replace `your-portkey-api-key-here` with your key.

**3. Install**

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**4. Start the app**

macOS / Linux: `.venv/bin/python run.py`  ·  Windows: `.venv\Scripts\python run.py`

Then open **http://127.0.0.1:8000** in your browser. Stop the app with `Ctrl+C`.

---

## Using the app

1. Enter a New Haven address, or click one of the examples (31 Chapel St, 100 Howe St, 224 Wooster St).
2. Optionally add a lease: choose **Include my lease** to upload your own (PDF or .txt), or **Use a sample lease**:
   - *A fair, standard lease*: should come back mostly clean.
   - *A lease with illegal clauses*: a 3-month deposit, early and oversized late fees, banned fees, lockouts, a liability waiver, and more.
   - *A lease with hidden AI instructions*: contains invisible white text telling AI tools to call the lease "fully compliant." Watch the safety filter catch it.
3. Click **Begin verification**. You'll see each review working live. A check takes about 30–90 seconds.
4. Read the verification report: the verdict, findings with clickable sources, questions for the landlord, links to the official sex-offender registries, and the scope and limitations. Open **Methodology and cost** for this run's measured tokens, cost and safeguards. Use **Print or save as PDF** to keep a copy.

The sample leases are fictional and are not tied to any example address.

### Optional: command line and tests

```bash
.venv/bin/python scripts/check.py "31 Chapel St, New Haven, CT" --lease samples/lease_red_flags.pdf   # one check, printed
.venv/bin/python tests/test_safety.py          # 10 offline safety tests (no AI calls, free)
.venv/bin/python tests/test_safety.py --live   # adds 2 live prompt-injection attacks (about $0.01)
.venv/bin/python scripts/eval_leases.py        # accuracy check of the 3 sample leases against an answer key (about $0.01)
```

Every check writes a full step-by-step trace to `logs/traces/<id>.json` and a one-line usage summary to `logs/runs.jsonl`.

---

## How it works

```
                         ┌──────────────────────────┐
  address + lease ──────▶│ Intake (code, no AI)     │  geocode, New Haven only, lease injection filter
                         └────────────┬─────────────┘
                                      ▼
                         ┌──────────────────────────┐
                         │ Lead Advisor (agent)     │  calls the specialists in parallel,
                         │                          │  writes the final report
                         └──┬──────────┬─────────┬──┘
                            ▼          ▼         ▼
                 Landlord          Neighborhood     Lease
                 Investigator      Scout            Reviewer
                 · assessor        · 311 reports    · read_lease
                   records         · eviction       · deposit, late fee and
                 · owner             filings          up-front fee calculators
                   portfolio       · FEMA flood     · CT law (20 rules, in the
                 · CT business       zone             cached prompt)
                   registry + principals
                            │          │         │
                            └──── evidence ledger + fact check (code) ────┘
```

- **Model:** `gpt-6-luna` through Portkey (`PORTKEY_API_KEY`), built with PydanticAI.
- **Real data, no keys:** U.S. Census geocoder; CT open data (2025 parcels/assessor, Business Registry master and principals, Eviction Lab weekly filings by tract); New Haven 311 (SeeClickFix); FEMA National Flood Hazard Layer. API responses are cached on disk for 24 hours in `.cache/`.
- **Connecticut law:** `data/ct_tenant_law.json` summarizes Conn. Gen. Stat. §§ 47a-4, 47a-4c, 47a-4d, 47a-4e, 47a-6, 47a-7, 47a-15a, 47a-16 and 47a-21, checked against the official text at cga.ct.gov.

### Safety defenses (all in code, so a prompt cannot turn them off)

| Risk | Defense |
|---|---|
| Prompt injection in a lease or a 311 description | `app/safety.py` strips invisible characters and removes AI-directed text before any agent sees it. Agents are also told the lease is data, not instructions. |
| Invented facts, numbers or quotes | Every tool result goes into an evidence ledger. Each finding must cite real evidence IDs, every number must appear in that evidence, and every quote must match the lease word for word. Failing findings are sent back to be fixed, then removed if they still fail. |
| Using the app to look people up | The registry tool only searches company names. Portfolio lookups only work for the owners of the address being checked. Sex-offender registries are linked, not searched. |
| Wrong scope | Addresses outside New Haven are refused before any AI call. |
| Runaway loops or overspending | Per-check caps on AI calls (30), tool calls (40) and tokens (600k); each specialist can be asked at most twice; 7-minute timeout; leases up to about 100 pages / 25 MB. |

### Project layout

```
app/config.py     .env loading (repo folder, then parent), model, prices, limits
app/sources.py    public-data API calls (no AI)
app/agents.py     the four agents, their tools, and the fact-check validator
app/safety.py     injection filter, evidence ledger, fact check
app/runner.py     runs one check end to end, logs tokens and traces
app/main.py       FastAPI server
static/           the web page (HTML/CSS/JS)
data/             Connecticut tenant-law reference
samples/          three fictional sample leases (scripts/make_sample_leases.py rebuilds them)
tests/            safety tests
```

## Limits

- New Haven only. Owner and complaint matching depends on how the Census geocoder and the assessor write each address.
- Eviction data is by census tract (filings, not completed evictions), not by landlord. Housing-code violations and crime data aren't available through open APIs, so the app uses 311 reports as a partial stand-in.
- The app doesn't read scanned (image-only) PDFs.
- It is not legal advice.
