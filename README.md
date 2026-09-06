# BTC Hourly Backtest

[![ci](https://github.com/dylan-fletcher/alpaca-takehome-assignment/actions/workflows/ci.yml/badge.svg)](https://github.com/dylan-fletcher/alpaca-takehome-assignment/actions/workflows/ci.yml)

A dbt + Postgres pipeline over Binance's 1-second BTCUSDT tape, answering an
analyst's two questions about hourly trading strategies: which hour of the day
returned the most, and which had the lowest maximum losses.

One command, against a throwaway Postgres container — reproducible from a clean
checkout plus the source CSV.

---

## Quickstart

```bash
./run.sh                                       # first load: 13.6 GB, ~10 min
./query.sh "select count(*) from raw.btc_1s"   # scratch queries
```

Put `half2_BTCUSDT_1s.csv` in the repo root first; it is not committed (see the
assignment brief), and neither is any other `.csv`.

Requirements: Docker (with WSL integration if you are on Windows) and
[uv](https://docs.astral.sh/uv/). Nothing else — `uv run` syncs the Python
environment from `uv.lock` on every invocation.

The load happens once and survives restarts, so `./run.sh` normally goes
straight to `dbt build`. `--reload` forces a fresh `COPY`; `--skip-load`
iterates on models without touching the data.

---

## Results

Over 1,281 trading days, 2021-02-24 to 2024-08-27 (UTC):

| | Answer | |
|---|---|---|
| **Q1** — biggest returns | **22:00 UTC** | +40.59% compounded |
| **Q2** — lowest maximum loss | **10:00 UTC** | max drawdown −9.03% |

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/ranking-dark.png">
  <img alt="Total return and maximum drawdown by hour of day" src="docs/ranking-light.png">
</picture>

**Q2 depends on what "maximum losses" means, so both readings are reported.**
The strategy closes every trade inside its own hour, but the analyst reinvests,
so their *capital* compounds across days — a run of ordinary losing days digs a
hole no single day explains. Measured that way (peak-to-trough drawdown of the
equity curve) the answer is **10:00**. Measured as the worst single day it is
**22:00**, which also wins Q1.

Drawdown leads, as the reading that matches the brief's reinvestment clause.
Worst-single-day is right only if the analyst re-stakes a fixed amount daily.

The panels above share one hour axis rather than each being sorted by its own
metric, which is what makes the disagreement visible: 22:00's losing days
*cluster*, so it has the shallowest worst day of any hour (−2.92%) but only the
fourth-shallowest drawdown (−14.10%). 10:00 has a deeper worst day (−3.57%) and
a much shallower drawdown, its losses being more scattered.

The full ranking prints on every `./run.sh`. The top and bottom of it:

| Hour (UTC) | Trades | Total return | Max drawdown | Worst day | Win rate |
|---|---|---|---|---|---|
| 22:00 | 1281 | **+40.59%** | −14.10% | **−2.92%** | 51.3% |
| 21:00 | 1281 | +40.13% | −22.55% | −5.15% | 54.5% |
| 10:00 | 1281 | +30.28% | **−9.03%** | −3.57% | 51.3% |
| … | | | | | |
| 01:00 | 1281 | −25.81% | −45.29% | −5.41% | 48.2% |
| 19:00 | 1281 | −26.95% | −36.05% | −6.24% | 51.7% |
| 03:00 | 1278 | −29.49% | −32.37% | −6.44% | 51.6% |

`Trades` is not constant across hours: the seven outages below cost some hours
a handful of days, so the count ranges 1277–1281 out of 1,281 possible. It is
exposed rather than hidden, so an uneven comparison stays visible.

**Read these as a description of the sample, not a forecast.** The spread from
best to worst hour is 70 percentage points, but it is drawn from 24 candidates
over a single 3.5-year window, and every hour's win rate sits within a few
points of a coin flip. Picking the best of 24 is a multiple-comparisons
exercise; the winner is partly noise.

### Repeating the analysis

The brief asks for this to be repeatable "for different hours and days of
entering the market". Each slice is a flag, not a model change:

```bash
./run.sh --skip-load --trade-hour 22                   # one hour of the day
./run.sh --skip-load --day-of-week 1                   # Mondays only
./run.sh --skip-load --start-date 2023-01-01 --end-date 2023-12-31
./run.sh --skip-load --trade-hour 22 --day-of-week 1   # they compose
```

Each maps to a dbt var of the same name, so the same slices are available to
`dbt build --vars` directly. Unset flags keep the `dbt_project.yml` defaults.

---

## Assumptions

Where the brief left room, this is how it was read.

| Assumption | Reasoning |
|---|---|
| **Buy at the open of `:00:00`, sell at the close of `:59:59`** | The literal "first second of the hour" and "last second of that hour". |
| **Returns compound** | The brief reinvests profits, so per-trade multipliers multiply. Summing percentages would assume a constant daily stake. |
| **"Maximum losses" means drawdown, with worst-single-day also reported** | Reinvestment makes the account an equity curve. Both are printed, since the brief admits both readings. |
| **2021-02-23 is excluded** | The file starts at 09:20:20, so hours 10–23 could trade that day and hours 00–09 could not. Left in, later hours get a free extra trade in a ranking that compares all 24. |
| **Hours missing a boundary price are dropped, not filled** | There was no trade to make. Substituting a nearby price would invent one the analyst could not have made. |
| **Returns are gross** | No fees or slippage — see [Limitations](#limitations). |
| **All timestamps are UTC** | Binance publishes UTC; nothing is converted. |

Compounding is not a cosmetic choice: it penalises volatility that an average
ignores. Over the untrimmed span the two disagree by up to 3 percentage points
— hour 15 is +10.85% compounded against +14.06% summed — and the orderings swap
several mid-table hours. They happen to agree on the winner, which was not
knowable in advance, so `avg_return_pct` is kept on the fact table to keep the
comparison visible.

---

## Modelling decisions

Data flows CSV → `raw` → `staging` → `intermediate` → `facts`.

**`raw` mirrors the CSV; cleaning happens in `staging`.** `raw.btc_1s` is a
byte-for-byte transcription, so defects stay observable and the load stays a
pure transcription. `stg_binance__btcusdt_1s_ohlcv` deduplicates, renames
(`open`/`close` are reserved words; volumes are split into base/quote) and drops
the unused `ignore` column. No joins, no aggregation, per
[dbt's staging guidance](https://docs.getdbt.com/best-practices/how-we-structure/2-staging).

**The uniqueness defect is tested twice, at different severities.** `warn` on
the source keeps the raw problem visible; `error` on the staging model proves
the deduplication works. If the source were fixed upstream or the dedup logic
regressed, one of the two would say so.

**The hourly model carries two prices and nothing else.** The obvious build is a
general hourly OHLCV rollup, but the strategy only ever touches `:00:00` and
`:59:59` — 3,600 seconds collapse to two numbers. Carrying the hour's high, low
and volume would cost the same scan but leave three columns nothing reads.
Adding them later is a one-line change.

**"First second of the hour" is a filter, not a ranking.** `:00:00` and
`:59:59` are calendar constants, selected with a `where` clause. Taking the
*first available* bar instead — `first_value`, or a windowed `row_number` —
would silently hand the backtest a price from 45 minutes into an hour the
exchange was down for, which is how 2021-04-25 08:00 would have acquired an
08:45:00 entry price.

**Two fact models, not one.** `fct_btcusdt_hourly_trades` is the per-trade
ledger; `fct_btcusdt_strategy_by_hour` aggregates it to the 24 rows the
questions are asked of. A 24-row summary cannot answer "what about Mondays?",
so the ledger carries `trade_date` and `day_of_week` and that question stays a
`group by`.

**Compounding lives in a macro.** Postgres has no `product()` aggregate, so
`macros/product.sql` reaches one through `a * b * c = exp(ln a + ln b + ln c)`.
That is a workaround for a missing function, not a statistical technique, but
raw `exp(sum(ln(x)))` in a select list has to be decoded by every reader.
`running_product()` is the same identity as a window, giving the equity curve
the drawdown is measured against. `tests/assert_product_macro_is_exact` pins
both to `2 * 3 * 4 = 24`.

**The drawdown high-water mark is seeded with the opening stake**
(`greatest(peak, 1)`). Without it, an hour that loses money before ever printing
a new high measures its fall from the dip rather than from par — hour 04 reads
−13.59% that way against a true −14.02%.

**Both "lowest maximum loss" columns are negative**, so the answer is the
*greatest* value — `order by ... desc`. Sorting either the intuitive way
returns precisely the wrong hour and looks entirely plausible doing it, which
is why `sql/final_query.sql` says so inline.

**Timestamps stay naive `timestamp`, not `timestamptz`.** Casting would make
`extract(hour from ...)` depend on the session `TimeZone` — turning the central
grouping key of an hourly backtest into something that varies by connection.

**`sql/load.sql` stays runnable by `psql`.** `db.statements()` strips psql
backslash meta-commands and expands `:'var'` placeholders itself, so the same
file works through `run.py` and through `psql -f /sql/load.sql` inside the
container. Useful when debugging the load.

---

## Data source

[Binance Bitcoin Dataset 1 Second — Part 2](https://www.kaggle.com/datasets/tzelal/binance-bitcoin-dataset-1s-timeframe-p2)
(Kaggle, user `tzelal`, CC0), collected from <https://data.binance.vision/> and
merged by the uploader. Part 1 (2017 onward) is not loaded.

| | |
|---|---|
| Rows loaded | 110,671,732 (110,671,480 after deduplication) |
| Coverage | 2021-02-23 09:20:20 → 2024-08-27 23:59:59 (UTC) |
| Grain | one row per second |
| Columns | 12, matching Binance's `/klines` payload |

Each row is one second of trading: **o**pen, **h**igh, **l**ow and **c**lose
price plus **v**olume — hence `stg_binance__btcusdt_1s_ohlcv`. The rest are
timestamps, a trade count, and volume split by whether the buyer was the taker.

The row count reconciles exactly: 110,671,733 lines is one header plus the
110,671,732 rows loaded. (The Kaggle page claims `110671734`, and its slicing
snippet would produce a headerless file; the distributed file does have a
header, so `COPY ... HEADER true` is correct.)

---

## Data quality findings

All figures are measured against the full 110.7M rows, never the fixture — the
anomalies are far too rare for a sample to catch.

**The three defects below are one phenomenon, not three.** Every gap falls on a
day that also carries a duplicate seam, and the seams begin exactly where data
resumes. Four of the seven gaps *start* at a truncated-`close_time` row. The
sequence at each maintenance window is: the last bar before the halt closes
early, data is missing for the outage, then on resume the uploader's merge
re-appends an overlapping window. Treating them as one root cause is what makes
the handling obvious — deduplicate, and surface the gaps for the rollup.

### 1. Duplicate timestamps — 56 timestamps, 252 spurious rows

The source is **not** unique on `open_time`. 308 rows share 56 timestamps and
collapse to exactly 56 distinct rows — **every duplicate is byte-identical
across all 12 columns**, which is what makes deduplication safe.

They form seven 8-second seams, one per affected day, with copy counts
descending in a staircase (`03:30:00` ×9, `03:30:01` ×8 … `03:30:07` ×2).
Affected days: 2021-03-06, 04-20, 04-25, 08-13, 09-29, 12-24, and 2023-03-24.
Each contributes exactly 8 timestamps and 44 rows.

**This is material.** Left in, the duplicates inflate hourly traded volume by
up to **+20.5%** (2021-08-13 06:00), then +12.6%, +10.2%, +7.8%, +4.7%, +2.2%
and +1.5% in the other six.

### 2. Missing seconds — 59,700 across 7 outage windows

The span holds 110,731,180 seconds; 110,671,480 rows remain after dedup, so
**59,700 seconds are absent** (0.054%).

| Gap starts after | Resumes at | Missing |
|---|---|---|
| 2021-03-06 01:59:59 | 2021-03-06 03:30:00 | 1h 30m |
| 2021-04-20 01:59:59 | 2021-04-20 04:30:00 | 2h 30m |
| 2021-04-25 04:00:58 | 2021-04-25 08:45:00 | 4h 44m |
| 2021-08-13 01:59:59 | 2021-08-13 06:30:00 | 4h 30m |
| 2021-09-29 06:59:59 | 2021-09-29 09:00:00 | 2h 00m |
| 2021-12-24 04:59:54 | 2021-12-24 05:00:36 | 41s |
| 2023-03-24 12:39:41 | 2023-03-24 14:00:00 | 1h 20m |

Kaggle's explorer reports "Missing 0%", but that means null *values* within
present rows — not absent timestamps. Only the second matters for a time series.

### 3. Truncated bars — 4 rows

`close_time` is normally `open_time + 999ms`. Four rows close early instead
(deltas of 146ms, 0ms, 362ms and 646ms), each at a collection boundary on a day
that also carries a duplicate seam. Each bar still sits in the correct second,
so hourly bucketing is unaffected. Noted rather than corrected.

### 4. Hourly completeness — 22 of 30,759 hours cannot be traded

The strategy transacts at two specific seconds, so the only completeness
question that matters is whether `:00:00` and `:59:59` exist. An hour missing
three seconds around `:17:30` is perfectly tradeable; an hour missing `:00:00`
is not, at any price.

| | |
|---|---|
| Hour buckets in span | 30,759 |
| Entirely absent (wholly inside an outage) | 13 |
| Present but missing a boundary second | 9 |
| **Untradeable** | **22** (0.07%) |

All 22 fall inside the seven outages, concentrated in 02:00–08:00 UTC.

Note that "incomplete" and "untradeable" are different sets, and counting on
`raw` gets it wrong: the duplicate seams inflate a bucket to 3,600 rows while it
still holds fewer than 3,600 distinct seconds. The rollup therefore reads the
deduplicated staging model, never `raw`.

### 5. Clean results

- `ignore` is `0` in all 110,671,732 rows, with no nulls.
- No row has `high < low`, or taker-buy volume exceeding total volume — which
  also confirms the base/quote labelling: `quote_volume / base_volume` lands
  inside that bar's own `[low, high]` on every row with volume, i.e. it is a
  VWAP in USDT per BTC.

---

## Testing

Severity encodes intent, so a red build always means the same thing.

- **Source tests run at `warn`.** They describe the file as it arrived. Known,
  characterised defects surface on every build without failing it.
- **Staging and fact tests run at `error`.** Staging asserts what we are willing
  to fail on (primary key, not-null on the transacted columns). Nothing in
  `models/facts/` is a property of the source — it is arithmetic this project
  performs, so a failure means the backtest computed the wrong number.
- **Tests report the actionable number.** Gap detection uses
  `dbt_utils.sequential_values`, which flags the row *after* each break and so
  reports **7 outage windows** rather than 59,700 missing seconds.
- **Related assertions are combined.** Each test is a full pass over 110M rows
  (30–75s), so OHLC coherence is one `expression_is_true` rather than four that
  fail for the same reason.

Three assertions span more than one model and live in `tests/` as singular
tests:

| Test | Catches |
|---|---|
| `assert_product_macro_is_exact` | either compounding macro drifting from `2 * 3 * 4 = 24` |
| `assert_all_24_hours_are_ranked` | an hour vanishing from the ranking, which would still return a plausible winner |
| `assert_summary_accounts_for_every_trade` | the two fact models' filters diverging |

The second is disabled when `trade_hour` is set, since 23 missing hours are then
the point rather than a defect.

`pytest` covers the Python half — `db.statements()` and the `run.py`
validators. CI runs SQL lint, `pytest` and `dbt parse` on every push; it cannot
run `dbt build`, which needs the gitignored 13.6 GB dataset.

---

## How it runs

`run.sh` is a thin shim; the logic is in `run.py`, which starts Postgres
(`docker compose up --wait`, blocking on the healthcheck), executes
`sql/load.sql`, runs `dbt build`, then runs `sql/final_query.sql` and redraws
the charts.

**The `COPY` is server-side.** The CSV is bind-mounted into the container and
the Postgres backend reads it directly, rather than streaming 13.6 GB through
the client — which is why the load takes **623 seconds** rather than hours.

The landing table is logged and lives on a named Docker volume, so it survives
`docker compose down`. An `UNLOGGED` table would `COPY` roughly twice as fast,
but Postgres truncates those after any unclean shutdown, and the repeated cost
here is iterating on models, not loading.

```
run.sh / run.py          entrypoint and orchestration
query.sh / query.py      scratch queries against the database
db.py                    shared connection + psql-script helpers
sql/load.sql             landing-table DDL and COPY
sql/final_query.sql      the two answers
models/staging/binance/  source definition, staging model, tests
models/intermediate/     hourly rollup to the grain the backtest trades on
models/facts/            the trade ledger and the per-hour ranking
macros/product.sql       multiply a column across a group, and along a curve
tests/                   singular dbt tests, plus tests/python/ for pytest
scripts/                 SQL linting and chart rendering
docker-compose.yml       Postgres 16
```

To exercise the load path without waiting ten minutes:

```bash
head -n 200001 half2_BTCUSDT_1s.csv > fixture.csv   # header + 200k rows
./run.sh --csv ./fixture.csv --reload
```

---

## Limitations

- **Only Part 2 is loaded**, so results cover 2021-02-24 onward. Part 1 holds
  2017-08-17 through 2021-02-23.
- **No trading costs.** Binance spot fees run ~0.1% per side, so ~0.2% a round
  trip — against a mean per-trade return of 0.03% on the winning hour, fees
  would swamp the edge entirely. The ranking describes price behaviour; it is
  not a claim that the strategy is profitable.
- **Returns are not tested for significance.** Picking the best of 24 hours is
  a multiple-comparisons exercise. `trade_count` and `stddev_return_pct` are
  carried so the spread stays visible, but nothing computes a confidence
  interval or corrects for the 24 simultaneous comparisons. That is the first
  thing to add before anyone acts on the ranking.
- **Fills are assumed at the printed price**, with no slippage and no size
  limit.
