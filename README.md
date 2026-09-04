# BTC Hourly Backtest

A dbt + Postgres pipeline over Binance's 1-second BTCUSDT tape, built to answer
an analyst's questions about hourly trading strategies.

Everything runs from one command against a throwaway Postgres container, so the
whole thing is reproducible from a clean checkout plus the source CSV.

---

## Quickstart

```bash
./run.sh                                       # full 13.6 GB load, ~7 minutes
./query.sh "select count(*) from raw.btc_1s"   # scratch queries
```

Put `half2_BTCUSDT_1s.csv` in the repo root first; it is not committed (see the
assignment brief), and neither is any other `.csv`. For a fast iteration loop,
carve off a sample and point `--csv` at it — this turns a 7-minute cycle into a
1-second one:

```bash
head -n 200001 half2_BTCUSDT_1s.csv > fixture.csv   # header + 200k rows
./run.sh --csv ./fixture.csv --reload
```

Requirements: Docker (with WSL integration enabled if you are on Windows) and
[uv](https://docs.astral.sh/uv/). Nothing else — `uv run` syncs the Python
environment from `uv.lock` on every invocation, so there is no venv to activate.

`./run.sh --help` lists the flags. The useful ones are `--reload` (force a fresh
COPY), `--skip-load` (iterate on models without touching the data) and
`--load-only` (load and stop).

---

## How it runs

`run.sh` is a thin shim; all the logic is Python in `run.py`, which does four
things:

1. **Start Postgres** — `docker compose up --wait`, blocking on the container's
   healthcheck so the load never races a database that is still initialising.
2. **Load the CSV** — executes `sql/load.sql` via psycopg2. The `COPY` is
   *server-side*: the CSV is bind-mounted into the container and the Postgres
   backend reads it directly, rather than streaming 13.6 GB through the client.
3. **`dbt build`** — builds the models and runs every test.
4. **Answer the questions** — runs `sql/final_query.sql` and prints the result.

The landing table is `UNLOGGED` on purpose. It is a reproducible staging area in
a disposable container, and skipping WAL roughly halves the load time. An
unclean shutdown truncates it; `./run.sh --reload` rebuilds it from the file.

The full load takes **395 seconds** for 110.7M rows on a laptop-class machine.

### Repo layout

```
run.sh / run.py          entrypoint and orchestration
query.sh / query.py      scratch queries against the database
db.py                    shared connection + psql-script helpers
sql/load.sql             landing-table DDL and COPY
models/staging/binance/  source definition, staging model, tests
docker-compose.yml       Postgres 16
```

---

## Data source

[Binance Bitcoin Dataset 1 Second — Part 2](https://www.kaggle.com/datasets/tzelal/binance-bitcoin-dataset-1s-timeframe-p2)
(Kaggle, user `tzelal`, CC0), itself collected from
<https://data.binance.vision/> and merged by the uploader.

The full dataset covers 2017-08-17 to 2024-08-28 and was split in half for
upload. **Part 2 is what this project loads**; Part 1 (2017 onward) is not
included. The CSV is never committed — see the assignment brief.

| | |
|---|---|
| Rows loaded | 110,671,732 (110,671,480 after deduplication) |
| Coverage | 2021-02-23 09:20:20 → 2024-08-27 23:59:59 (UTC) |
| Grain | one row per second |
| Columns | 12, matching the Binance kline API payload |

**The row count reconciles exactly.** The file is 110,671,733 lines: one header
plus 110,671,732 data rows, which is what we load. Two notes where the Kaggle
page disagrees with the artifact it ships, neither of which affects us:

- The page states this part has `110671734` rows — off by 2 against the file.
- The slicing snippet in the description (`tail -n +{midpoint + 1}`) would
  produce a file with **no header**, because the header goes to Part 1. The
  distributed file *does* have a header, so `COPY ... HEADER true` is correct.
  Worth stating explicitly: had the file matched the snippet, that setting would
  have silently discarded a real data row.

---

## Data quality findings

All figures below are measured against the full 110.7M rows, not a sample.

### 1. Duplicate timestamps — 56 timestamps, 252 spurious rows

The source is **not** unique on `open_time`. 308 rows share 56 timestamps, and
those 308 rows collapse to exactly 56 distinct rows — **every duplicate is
byte-identical across all 12 columns.**

They are not scattered. They form seven 8-second seams, one per affected day,
with copy counts descending in a perfect staircase:

```
2021-03-06 03:30:00   9 copies
2021-03-06 03:30:01   8 copies
...
2021-03-06 03:30:07   2 copies
```

Affected days: 2021-03-06, 2021-04-20, 2021-04-25, 2021-08-13, 2021-09-29,
2021-12-24, 2023-03-24. Each contributes exactly 8 timestamps and 44 rows. The
shape is characteristic of overlapping archive windows being concatenated during
the uploader's merge.

**This is material to the backtest.** The duplicates land in seven specific
hours and inflate hourly traded volume there:

| Hour (UTC) | Volume inflation |
|---|---|
| 2021-08-13 06:00 | +20.5% |
| 2021-09-29 09:00 | +12.6% |
| 2023-03-24 14:00 | +10.2% |
| 2021-03-06 03:00 | +7.8% |
| 2021-04-20 04:00 | +4.7% |
| 2021-04-25 08:00 | +2.2% |
| 2021-12-24 05:00 | +1.5% |

### 2. Missing seconds — 59,700 across 7 outage windows

The span from first to last bar is 110,731,180 seconds. After deduplication
110,671,480 rows remain, so **59,700 seconds are absent** (0.054%).

They are not scattered either. `dbt_utils.sequential_values` reports **7**
failures, not 59,700, because it flags the row *after* each break — so the
missing time collapses into seven discrete outages:

| Gap starts after | Resumes at | Missing |
|---|---|---|
| 2021-03-06 01:59:59 | 2021-03-06 03:30:00 | 1h 30m |
| 2021-04-20 01:59:59 | 2021-04-20 04:30:00 | 2h 30m |
| 2021-04-25 04:00:58 | 2021-04-25 08:45:00 | 4h 44m |
| 2021-08-13 01:59:59 | 2021-08-13 06:30:00 | 4h 30m |
| 2021-09-29 06:59:59 | 2021-09-29 09:00:00 | 2h 00m |
| 2021-12-24 04:59:54 | 2021-12-24 05:00:36 | 41s |
| 2023-03-24 12:39:41 | 2023-03-24 14:00:00 | 1h 20m |

Kaggle's data explorer reports "Missing 0%", but that refers to null *values*
within present rows — not absent timestamps. The two are different questions and
only the second one matters for a time-series backtest.

### 2a. All three defects are the same seven events

The duplicate seams, the gaps and the truncated bars are one phenomenon, not
three. Every gap falls on a day that also carries a duplicate seam, and the
seams begin **exactly where data resumes**: the 2021-03-06 outage ends at
03:30:00, and the duplicated seconds are 03:30:00 through 03:30:07. Four of the
seven gaps *start* at a truncated-`close_time` row.

The sequence at each maintenance window is:

1. The last bar before the halt closes early (truncated `close_time`).
2. Data is missing for the duration of the outage.
3. On resume, the uploader's merge re-appends an overlapping window, producing
   8 seconds of byte-identical duplicates with a descending copy count.

Treating them as one root cause is what makes the handling straightforward:
deduplicate, and surface the gaps for the hourly rollup to decide about.

### 3. Truncated bars — 4 rows

`close_time` is normally `open_time + 999ms`. Four rows close early instead:

| `open_time` | delta | trades |
|---|---|---|
| 2021-04-25 04:00:58 | 146ms | 1 |
| 2021-08-13 01:59:59 | 0ms | 0 |
| 2021-12-24 04:59:54 | 362ms | 11 |
| 2023-03-24 12:39:41 | 646ms | 0 |

All four fall on days that also carry duplicate seams — the same collection
boundaries. Each bar still sits in the correct second, so hourly bucketing is
unaffected. Noted rather than corrected.

### 4. Clean results

- `ignore` is `0` in all 110,671,732 rows, with no nulls. (Kaggle hedges that it
  "typically contains zeros or null values"; in this file it is uniformly 0.)
- No row has `high < low`.
- No row has taker-buy volume exceeding total volume, in either base or quote
  terms — which also confirms the base/quote labelling: `quote_volume /
  base_volume` lands inside that bar's own `[low, high]` on every row with
  volume, i.e. it is a VWAP in USDT per BTC.

---

## Design decisions

**Duplicates are removed in staging, not in the landing table.** `raw.btc_1s`
stays a faithful byte-for-byte mirror of the CSV, so the defect remains
observable and the load stays a pure transcription. `stg_binance__btcusdt_1s_klines`
applies `distinct on (open_time)`. Because every duplicate is identical,
retaining an arbitrary copy discards no information.

*This is the decision most worth a second opinion.* The alternative — leaving
staging 1:1 and deduplicating in the intermediate layer — is defensible, but it
pushes the burden onto every downstream consumer. To reverse it, delete the
`deduplicated` CTE and move the logic into the hourly rollup.

**The uniqueness defect is tested in both places, at different severities.** A
`warn`-severity `unique` test on the *source* keeps the raw problem visible on
every build. An `error`-severity `unique` test on the *staging model* proves the
deduplication actually works. If the source were silently fixed upstream, or the
dedup logic regressed, one of the two would tell us.

**Timestamps stay naive `timestamp`, not `timestamptz`.** Binance data is UTC.
Casting to `timestamptz` would make `extract(hour from ...)` depend on the
session `TimeZone` setting — turning the central grouping key of an hourly
backtest into something that silently varies by connection. Naive UTC is
deterministic.

**Staging renames, drops and deduplicates — nothing else.** No joins, no
aggregation, per [dbt's staging guidance](https://docs.getdbt.com/best-practices/how-we-structure/2-staging).
`ignore` is dropped. `open`/`close` become `open_price`/`close_price`, both for
clarity and because they are reserved words. Volumes are disambiguated into base
(BTC) and quote (USDT).

**One properties file per source directory, not per model.** `_binance__staging.yml`
holds the source and the staging model together. dbt's guide shows a YAML file
per model; at this project's size that is more files to keep in sync than it is
worth, and keeping a column's source caveats next to its downstream tests makes
both easier to read.

**`sql/load.sql` remains runnable by `psql`.** `db.statements()` strips psql
backslash meta-commands and expands `:'var'` placeholders itself, so the same
file works through `run.py` and through
`psql -f /sql/load.sql` inside the container. That escape hatch is useful when
debugging the load.

---

## Testing strategy

Tests are split by where a failure would mean something different.

**Source tests describe the file as it arrives.** Known defects run at `warn`,
so they surface on every build without turning the pipeline red over something
we have already characterised and handled.

**Staging tests are assertions we are willing to fail the build on.** Primary
key uniqueness and not-null on the columns the backtest transacts against.

**Gap detection** uses `dbt_utils.sequential_values` with `interval: 1,
datepart: second` on `bar_open_at`. The dataset is nominally a continuous
1-second series, so any step other than +1s is missing data. It runs at `warn`
because the gaps are a property of the source rather than a pipeline bug — but
the count is printed on every build, so a *change* in it is visible. It reports
7 (outage windows), not 59,700 (missing seconds), which is the more actionable
number.

**Invariant tests** (`dbt_utils.expression_is_true`) cover OHLC coherence
(`high >= max(open, close)`, `low <= min(open, close)`, `high >= low`) and taker
volume never exceeding total volume. These are combined into two expressions
rather than six separate tests: each test is a full pass over 110M rows, and the
components fail for the same underlying reason.

---

## Known limitations and next steps

- **Only Part 2 is loaded.** 2017-08-17 through 2021-02-23 lives in Part 1 and
  is not included, so results cover 2021-02-23 onward.
- **Intermediate and facts layers are not built yet.** `models/intermediate/`
  and `models/facts/` are empty, and `sql/final_query.sql` does not exist —
  `run.sh` step 4 skips gracefully until it does.
- **The gaps are surfaced but not handled.** 59,700 missing seconds across 7
  outages are currently just counted. Whether an hour with missing seconds should be
  excluded from the backtest, forward-filled, or used as-is is a modelling
  decision that belongs with the hourly rollup.
- **Staging is a view over 110M rows.** If the hourly rollup proves slow, the
  fix is to materialize this model as a table; it is a one-line config change.
