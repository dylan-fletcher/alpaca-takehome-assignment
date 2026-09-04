# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Always run through `uv`

This project has no activated virtualenv and nothing on PATH. **Every Python or
dbt command must go through `uv run`**, which syncs the environment from
`uv.lock` before executing:

```bash
uv run -- dbt build
uv run -- python run.py --help
```

Use the `--` separator. Without it `uv run` will try to claim flags like
`--reload` for itself instead of passing them to the script.

Do **not** invoke `.venv/bin/python`, `.venv/bin/dbt`, or a bare `dbt`/`python`.
A `.venv/` directory exists as a uv implementation detail; targeting it directly
bypasses dependency syncing and will drift. `run.py` shells out to dbt as
`["uv", "run", "--", "dbt", ...]` for the same reason — keep it that way.

Managing dependencies: use `uv add <package>` (`uv add --dev <package>` for
dev-only, `uv remove <package>` to drop one). It updates `pyproject.toml` and
`uv.lock` together in one step. Do not hand-edit `pyproject.toml`, and do not
`pip install`.

## Commands

```bash
./run.sh                              # full pipeline: db -> load -> dbt build -> final query
./run.sh --csv ./fixture.csv --reload # fast loop against a small sample (~1s vs ~7min)
./run.sh --skip-load                  # iterate on models, leave the data alone
./run.sh --load-only                  # load and stop
./run.sh --help

./query.sh "select count(*) from raw.btc_1s"   # scratch query, prints a table
./query.sh -f sql/some_file.sql
./query.sh                                      # interactive prompt

uv run -- dbt build                                        # all models + tests
uv run -- dbt build --select stg_binance__btcusdt_1s_klines # one model and its tests
uv run -- dbt test  --select source:binance                 # source tests only
uv run -- dbt parse                                         # validate YAML without touching the DB
uv run -- dbt deps                                          # after editing packages.yml
```

`./run.sh` and `./query.sh` are deliberately thin bash shims that locate `uv`
and exec the matching `.py`. Put logic in the Python, not the shim.

A full load takes ~7 minutes for 110.7M rows. Generate a fixture with
`head -n 200001 half2_BTCUSDT_1s.csv > fixture.csv` and use `--csv` while
iterating. Note `*.csv` is gitignored, so the dataset and fixture are never
committed.

Requires Docker running with WSL integration enabled (Docker Desktop →
Resources → WSL Integration). Without it, `docker` resolves to a Windows `.exe`
shim that errors with "could not be found in this WSL 2 distro".

## Pull requests

This repo is on GitHub (`origin`, `dylan-fletcher/alpaca-takehome-assignment`)
and uses the `gh` CLI. The default branch is `master`; work lands on `feat/*`
branches and merges back through a PR — never commit to `master` directly.

```bash
git switch -c feat/<topic>
git add -A && git commit -m "..."
git push -u origin HEAD          # -u on the first push, so later pushes are bare

gh pr create --base master --title "..." --body-file <path>
gh pr create --base master --fill   # reuse the commit subject/body instead
```

Once it exists:

```bash
gh pr view --web     # open in a browser
gh pr diff           # review the diff locally
gh pr status         # where the current branch's PR stands
gh pr checks         # CI results
```

Notes:

- **Always pass `--title`/`--body-file` or `--fill`.** A bare `gh pr create`
  prompts interactively, and interactive prompts do not work in this
  environment — the command will appear to hang.
- **Prefer `--body-file` over `--body`** for anything longer than a sentence.
  PR bodies here contain backticks, code fences and newlines, all of which get
  mangled by shell quoting when passed inline.
- `gh` is already authenticated. If a call 401s, check `gh auth status` before
  assuming the command is wrong.
- Verify what a commit will actually contain first. The dataset (`*.csv`),
  `target/`, `logs/`, `dbt_packages/` and `.user.yml` are all gitignored and
  must stay that way; `package-lock.yml` and `uv.lock` are tracked on purpose.

## Architecture

Data flows CSV → `raw` → `staging` → `intermediate` → `facts`, with dbt owning
everything from `staging` onward.

**`run.py` orchestrates; `sql/load.sql` and dbt do the work.** `run.py` starts
Postgres via Docker Compose, executes the load, invokes `dbt build`, then runs
`sql/final_query.sql`. It is the only place that knows the step order.

**The COPY is server-side.** `docker-compose.yml` bind-mounts the CSV to
`/data/dataset.csv` inside the container so the Postgres backend reads the file
directly. 13.6 GB never crosses the client connection. This is why the load is
minutes rather than hours — do not replace it with a client-side `copy_from`.

**`db.py` is shared by `run.py` and `query.py`**: connection handling (mirroring
`profiles.yml`, honouring `PG*` env vars), plus `statements()`, which splits a
psql-style script into psycopg2-executable statements. `statements()` strips
backslash meta-commands and expands `:'var'` placeholders, which is what keeps
`sql/load.sql` runnable *both* through `run.py` and via
`psql -f /sql/load.sql` inside the container. Preserve that dual-compatibility.

**`raw` is a faithful mirror; `staging` is where cleaning happens.**
`raw.btc_1s` is a byte-for-byte transcription of the CSV, `UNLOGGED` and
rebuilt by `DROP TABLE` + `COPY` on every `--reload`. Data defects are
corrected in the staging model, never in the load.

**Test severities encode intent.** Source tests run at `warn` and describe the
file as it arrives (known upstream defects, surfaced but not blocking). Staging
tests run at `error` and assert things we are willing to fail a build on. If you
add a test, decide which of those two it is.

See `README.md` for the data-quality findings and the reasoning behind each
modelling decision.

## Testing and data assumptions

**Every assumption about the data must be expressed as a test, not as prose.** A
sentence in a YAML `description:` is unverified by definition — it reads as
authoritative, is never executed, and silently rots. If you catch yourself
writing "always", "never", or "exactly" about this dataset, that claim belongs
in `data_tests:`, where it either passes or fails on the next build.

Rules that follow:

- **Validate against the full table, never the fixture.** `fixture.csv` exists
  to make iteration fast, not to establish facts. The anomalies here are rare —
  4 truncated bars and 56 duplicated timestamps out of 110M rows — and a sample
  will miss them.
- **Characterise before handling.** When a test fails, establish how many rows,
  what the pattern is, and what it does to downstream numbers *before* deciding
  what to do. The duplicates turned out to be byte-identical and clustered into
  seven seams, which is what made deduplication obviously safe. None of that was
  knowable from the failure count alone.
- **Choose severity deliberately.** `warn` for a known property of the source
  that has been characterised and accepted; `error` for something that must
  hold. A test that is neither is noise.
- **Prefer the test that reports the actionable number.** `sequential_values`
  reports 7 outage windows rather than 59,700 missing seconds — the same fact,
  but one of them tells you what to go look at.
- **Mind the cost.** Each test is a full pass over 110M rows (30-75s). Combine
  related assertions into one `expression_is_true` rather than adding six tests
  that all fail for the same reason.
- **Record it in `README.md`** whenever a finding changes a modelling decision,
  with the numbers that justified it.

## Gotchas

**Keep this section current — this is a standing instruction, not a suggestion.**
If you hit an error while working in this repo that took real effort to diagnose
and would plausibly bite the next person, add it here as part of the same change
that resolves it. Don't wait to be asked, and don't leave it for a follow-up.

What belongs here: non-obvious tool behaviour, silent misconfiguration, a
surprising Postgres or dbt semantic, anything where the error message points
somewhere other than the actual cause. Write one line on the symptom and one on
why it happens — the symptom is what a future reader will search for.

What does not: one-off typos, or anything the error message already explains
plainly. This list is only useful while every entry earns its place.

The same applies to the rest of the file. If you change a command, a flag, or
how the pipeline is wired, update the section that describes it in the same
commit, so this file never drifts from the code.

Each entry below is a failure already paid for once:

- **`CSV_FILE` must be an absolute path.** Compose reads a mount source with no
  leading `./` or `/` as a *named volume*, and `Path()` strips `./`. `run.py`
  calls `.resolve()` for this reason.
- **`to_regclass` cannot guard a table reference in the same statement.**
  Postgres resolves table names at parse time, so
  `CASE WHEN to_regclass(...) IS NULL THEN 0 ELSE (SELECT count(*) FROM t) END`
  still fails when `t` is absent. Check existence in a separate query.
- **Generic test arguments nest under `arguments:`** in dbt 1.12. Top-level
  args (`values:`, `expression:`, `interval:`) parse but emit deprecations.
- **Timestamps are intentionally naive `timestamp`, not `timestamptz`.** The
  data is UTC; casting would make `extract(hour from ...)` depend on the session
  `TimeZone` and make the hourly backtest non-deterministic. Don't "fix" this.
