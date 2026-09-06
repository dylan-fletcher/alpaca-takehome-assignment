# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Always run through `uv`

There is no activated virtualenv and nothing on PATH. **Every Python or dbt
command goes through `uv run`**, which syncs from `uv.lock` before executing:

```bash
uv run -- dbt build
uv run -- python run.py --help
```

Use the `--` separator, or `uv run` claims flags like `--reload` for itself.

Do **not** invoke `.venv/bin/python`, `.venv/bin/dbt`, or a bare `dbt`/`python`.
`.venv/` is a uv implementation detail; targeting it bypasses dependency syncing
and drifts. `run.py` shells out as `["uv", "run", "--", "dbt", ...]` for the
same reason -- keep it that way.

Dependencies: `uv add <package>` (`--dev` for dev-only, `uv remove` to drop).
It updates `pyproject.toml` and `uv.lock` together. Do not hand-edit
`pyproject.toml`, and do not `pip install`.

## Commands

```bash
./run.sh                              # full pipeline: db -> load -> dbt build -> final query
./run.sh --csv ./fixture.csv --reload # fast loop against a small sample (~1s vs ~10min)
./run.sh --skip-load                  # iterate on models, leave the data alone
./run.sh --load-only                  # load and stop
./run.sh --trade-hour 22 --day-of-week 1 --start-date 2023-01-01   # slice the backtest
./run.sh --help

./query.sh "select count(*) from raw.btc_1s"   # scratch query, prints a table
./query.sh -f sql/some_file.sql
./query.sh                                      # interactive prompt

uv run -- dbt build                                        # all models + tests
uv run -- dbt build --select stg_binance__btcusdt_1s_ohlcv # one model and its tests
uv run -- dbt test  --select source:binance                 # source tests only
uv run -- dbt parse                                         # validate YAML without touching the DB
uv run -- dbt deps                                          # after editing packages.yml

uv run -- pytest                             # Python tests (tests/python/)
uv run -- python scripts/make_charts.py      # redraw docs/ranking-*.png
uv run -- python scripts/lint_sql.py         # lint every .sql file (add a path for one)
uv run -- python scripts/lint_sql.py --staged # lint what is staged for commit
uv run -- sqlfluff fix .                     # auto-fix what is safely fixable
```

`./run.sh` and `./query.sh` are thin bash shims that locate `uv` and exec the
matching `.py`. Put logic in the Python, not the shim.

A full load takes ~10 minutes for 110.7M rows and only happens once -- `run.py`
skips it when the table is populated. For a fixture:
`head -n 200001 half2_BTCUSDT_1s.csv > fixture.csv`. `*.csv` is gitignored, so
neither the dataset nor the fixture is ever committed.

Requires Docker with WSL integration enabled (Docker Desktop -> Resources -> WSL
Integration). Without it `docker` resolves to a Windows `.exe` shim that errors
with "could not be found in this WSL 2 distro".

## Architecture

Data flows CSV -> `raw` -> `staging` -> `intermediate` -> `facts`, with dbt
owning everything from `staging` onward.

**`run.py` orchestrates; `sql/load.sql` and dbt do the work.** It starts
Postgres, executes the load, invokes `dbt build`, runs `sql/final_query.sql`,
then redraws the charts. It is the only place that knows the step order.

**The COPY is server-side.** `docker-compose.yml` bind-mounts the CSV to
`/data/dataset.csv` so the Postgres backend reads it directly; 13.6 GB never
crosses the client connection. Do not replace it with a client-side `copy_from`.

**`db.py` is shared by `run.py` and `query.py`**: connection handling (mirroring
`profiles.yml`, honouring `PG*` env vars) plus `statements()`, which splits a
psql-style script into psycopg2-executable statements. It strips backslash
meta-commands and expands `:'var'` placeholders, which keeps `sql/load.sql`
runnable *both* through `run.py` and via `psql -f /sql/load.sql` inside the
container. Preserve that dual-compatibility -- `tests/python/test_db.py` pins it.

**`raw` is a faithful mirror; `staging` is where cleaning happens.** `raw.btc_1s`
is a byte-for-byte transcription, rebuilt by `DROP TABLE ... CASCADE` + `COPY`
on `--reload`. Data defects are corrected in the staging model, never the load.

**The fact layer answers the questions; the intermediate model feeds it.**
`fct_btcusdt_hourly_trades` is the per-trade ledger, `fct_btcusdt_strategy_by_hour`
aggregates it to 24 rows. Compounding uses the `product()` macro and the
drawdown's equity curve uses `running_product()`, never a raw
`exp(sum(ln(...)))` -- keep the reasoning in the macros, not at each call site.

**Q2 has two answers on purpose.** "Maximum losses" reads either as the deepest
peak-to-trough drawdown of the compounded stake or as the worst single day, and
on this data they name different hours (10:00 vs 22:00). Both are columns on the
summary and both print in `sql/final_query.sql`. Drawdown leads, as the reading
consistent with reinvestment. Don't collapse this back to one number.

**Five vars parameterise the backtest**, all in `dbt_project.yml`: `trade_hour`,
`trade_day_of_week` (ISO, 1=Monday), `initial_units`, `backtest_start_date`
(2021-02-24, excluding the partial first day) and `backtest_end_date`. `run.py`
plumbs all but `initial_units` through to `dbt build --vars`, forwarding only
the flags actually given so an unset one defers to the default rather than
overriding it with null. Anything that filters or scales the backtest belongs
here, not hard-coded in a model.

See `README.md` for the data-quality findings and the reasoning behind each
modelling decision.

## Pull requests

`origin` is `dylan-fletcher/alpaca-takehome-assignment`. The default branch is
`master`; work lands on `feat/*` branches through a PR -- never commit to
`master` directly. **Always squash and merge, and always delete the branch.**

```bash
git switch -c feat/<topic>
git add -A && git commit -m "..."
git push -u origin HEAD

gh pr create --base master --title "..." --body-file <path>
gh pr checks                     # CI results
gh pr merge <number> --squash --delete-branch
git switch master && git pull
```

- **Always pass `--title`/`--body-file` or `--fill`.** A bare `gh pr create`
  prompts interactively, and interactive prompts hang in this environment.
- **Prefer `--body-file` over `--body`.** PR bodies here contain backticks and
  code fences, which shell quoting mangles.
- `gh` is already authenticated. If a call 401s, check `gh auth status` first.
- **Verify what a commit will contain before making it.** `git add -A` has
  picked up stray files here before. The dataset (`*.csv`), `target/`, `logs/`,
  `dbt_packages/`, `.user.yml` and `*:Zone.Identifier` are gitignored and must
  stay that way; `package-lock.yml` and `uv.lock` are tracked on purpose.

## SQL style

Enforced by SQLFluff (`.sqlfluff`) plus one local rule in `scripts/sql_style.py`.
Three layers call the same `scripts/lint_sql.py`:

| Layer | Fires on | Config |
|---|---|---|
| `PostToolUse` hook | Claude's Write/Edit of a `.sql` file | `.claude/settings.json` |
| `PreToolUse` hook | Claude running `git commit` (lints staged SQL) | `.claude/settings.json` |
| GitHub Actions | every push to `master` and every PR | `.github/workflows/ci.yml` |

The hooks are a fast feedback loop, not enforcement -- they only see Claude's
actions, and `PostToolUse` is blind to edits made through Bash. **CI is the only
layer that gates anything.** It also runs `pytest` and `dbt parse`; it cannot
run `dbt build`, which needs the gitignored 13.6 GB dataset.

The conventions:

- **Lower case keywords**, 80-character lines, trailing commas.
- **Import CTEs at the top**, each just `select * from {{ source(...) }}` or
  `{{ ref(...) }}`, so a model's dependencies are readable from its first lines.
- **Keywords start their line.** `select`, `from`, `where` are never trailed by
  the first target.
- **A select list is one contiguous block.** Group it with `---------- banner`
  comments, not blank lines -- the `SQ01` rule in `scripts/sql_style.py`,
  which exists because SQLFluff has no setting to forbid them.
- **Aliases align**, so a rename block reads as a mapping.
- **Comments earn their length.** Say why, in a few lines. Numbers and full
  workings go in `README.md`.

`sqlfluff fix` handles most of this. It cannot fix `SQ01`, long lines, or an
over-long comment -- the ones worth thinking about anyway.

## Testing and data assumptions

**Every assumption about the data must be expressed as a test, not as prose.** A
sentence in a YAML `description:` is unverified by definition -- it reads as
authoritative, is never executed, and silently rots. If you catch yourself
writing "always", "never" or "exactly" about this dataset, that claim belongs in
`data_tests:`, where it passes or fails on the next build.

- **Validate against the full table, never the fixture.** The anomalies are rare
  -- 4 truncated bars and 56 duplicated timestamps out of 110M rows -- and a
  sample will miss them.
- **Characterise before handling.** Establish how many rows, what the pattern
  is, and what it does to downstream numbers *before* deciding what to do. The
  duplicates turned out byte-identical and clustered into seven seams, which is
  what made deduplication obviously safe; none of that was knowable from the
  failure count.
- **Choose severity deliberately.** `warn` for a known, characterised property
  of the source; `error` for something that must hold. A test that is neither is
  noise.
- **Prefer the test that reports the actionable number.** `sequential_values`
  reports 7 outage windows rather than 59,700 missing seconds.
- **Mind the cost.** Each test is a full pass over 110M rows (30-75s). Combine
  related assertions into one `expression_is_true` rather than six that fail for
  the same reason.
- **Record it in `README.md`** whenever a finding changes a modelling decision,
  with the numbers that justified it.

## Gotchas

**Keep this section current -- a standing instruction, not a suggestion.** If an
error takes real effort to diagnose and would bite the next person, add it here
as part of the same change that resolves it. One line on the symptom (what a
future reader will search for), one on why it happens.

Not for one-off typos or anything the error message already explains plainly.
The list is only useful while every entry earns its place.

Each entry below is a failure already paid for once:

- **A PR that "won't merge" right after a force-push is usually just
  recomputing.** `gh pr view --json mergeable` returns `UNKNOWN` and the web UI
  shows a spinner while GitHub re-runs its test-merge; querying it is what
  nudges the job along. Poll until it reports `MERGEABLE / CLEAN` rather than
  hunting for branch protection or failing checks.
- **`CSV_FILE` must be an absolute path, and must exist.** Compose reads a
  mount source with no leading `./` or `/` as a *named volume*, so `run.py`
  calls `.resolve()`. A source that does not exist is worse: Compose creates a
  root-owned *directory* there, which needs sudo to remove and makes the next
  load fail with "is a directory". `run.py` mounts `/dev/null` instead when
  there is no dataset, which is the normal state under `--skip-load`.
- **Never move the dataset to test the load path.** `/tmp` is a 3.9 GB tmpfs;
  a `mv` of the 13.6 GB CSV onto it silently truncates the file at the ENOSPC
  boundary. Point `--csv` at a throwaway path instead -- that is what the flag
  is for.
- **`to_regclass` cannot guard a table reference in the same statement.**
  Postgres resolves table names at parse time, so
  `CASE WHEN to_regclass(...) IS NULL THEN 0 ELSE (SELECT count(*) FROM t) END`
  still fails when `t` is absent. Check existence in a separate query.
- **Generic test arguments nest under `arguments:`** in dbt 1.12. Top-level
  args (`values:`, `expression:`, `interval:`) parse but emit deprecations.
- **Timestamps are intentionally naive `timestamp`, not `timestamptz`.** The
  data is UTC; casting would make `extract(hour from ...)` depend on the session
  `TimeZone` and make the hourly backtest non-deterministic. Don't "fix" this.
- **SQLFluff's `RF04` ignores `unquoted_identifiers_policy` for DDL.** The
  policy already defaults to `aliases`, yet the rule still fires on column names
  in `create table`. `ignore_words` is the only lever that works there.
- **`FluffConfig.from_path()` walks up from the path you hand it**, not from the
  project. Give it a file outside the repo and it finds no `.sqlfluff` and dies
  with "No dialect was specified". `scripts/sql_style.py` anchors it to ROOT.
- **`allow_implicit_indents` is deprecated** in favour of
  `implicit_indents = allow`. The old spelling still works but prints a
  paragraph of warning on every single lint, which buries real findings.
- **CI must run `dbt deps` before `dbt parse`.** `dbt_packages/` is gitignored
  and the staging tests use `dbt_utils`, so a clean checkout fails parse with
  "found only 0 package(s) installed" -- which reads like a dbt bug, not a
  missing step.
- **`dbt parse` needs no database.** It resolves `profiles.yml` from the project
  directory (there is no `~/.dbt`) and never opens a connection, which is what
  makes it usable in CI. `dbt build` is not, and cannot be: the dataset is
  13.6 GB and gitignored.
- **GitHub Actions does not clone for you.** Unlike GitLab CI, every job needs
  an explicit `actions/checkout` step first.
- **SQLFluff needs `load_macros_from_path` to see project macros.** Without it
  every model calling one fails with `TMP: Undefined jinja template variable`
  pointing at the *call site*, which reads as a typo in the model rather than a
  missing search path. Set in `.sqlfluff` under `[sqlfluff:templater:jinja]`.
- **SQLFluff's stubbed `var()` never returns `None`.** So `{% if var('x') is
  not none %}` branches are always rendered during linting, even when the
  default build never emits them. They must satisfy the indentation rules
  regardless -- and a branch that renders to nothing but a bare `and` will fail
  to parse.
- **`sqlfluff fix` reorders select lists (ST06) but leaves comments put.**
  Banner comments do not travel with the columns they head, so after a fix they
  can silently describe the wrong block. Re-read the select list after fixing.
- **Singular tests that `ref()` nothing are invisible to `--select model+`.**
  A macro unit test has no model in its lineage, so it never runs in a selected
  build and only appears in a bare `dbt build`. Select it by name to run it
  alone.
- **`drop table raw.btc_1s` needs `cascade` once dbt has run.** The staging
  view depends on it, so a bare drop fails with `DependentObjectsStillExist`
  mid-`--reload`. The next `dbt build` recreates the view.
