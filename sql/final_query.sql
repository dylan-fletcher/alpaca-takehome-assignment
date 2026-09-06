-- The analyst's two questions, answered from analytics_facts.
--
-- Run by `run.py` as the last step of `./run.sh`, and equally by
-- `psql -f /sql/final_query.sql` inside the container. Plain SQL on purpose:
-- every decision that needed explaining lives in the dbt models, so this file
-- only has to ask.
--
-- The `noqa: ST06` markers hold the column order. ST06 wants plain columns
-- ahead of expressions, but this file's select lists *are* the output someone
-- reads, and the hour being answered has to come first.


-- Question 1: which hour of the day had the biggest returns?
--
-- Compounded across every trade in the hour, because the analyst reinvests
-- each trade's proceeds into the next.
select
    'Q1: biggest returns'                as question,  -- noqa: ST06
    to_char(trade_hour, 'FM00') || ':00' as hour_utc,
    trade_count,
    round(total_return_pct, 2)           as total_return_pct,
    round(ending_units, 4)               as ending_units,
    round(win_rate_pct, 1)               as win_rate_pct
from analytics_facts.fct_btcusdt_strategy_by_hour
order by total_return_pct desc
limit 1;


-- Question 2: which hour of the day had the lowest maximum losses?
--
-- "Maximum losses" has two defensible readings, and on this data they name
-- different hours, so both are reported rather than one chosen silently:
--
--   a) the deepest peak-to-trough fall of the compounded stake (drawdown)
--   b) the largest loss on any single day
--
-- (a) is the primary answer. The brief has the analyst reinvesting, so their
-- capital compounds across days even though each trade closes inside its own
-- hour -- a run of ordinary losing days digs a hole no single day explains.
-- (b) is the right answer only if the analyst re-stakes a fixed amount daily.
--
-- Both columns are negative, so "lowest maximum loss" is the *greatest*
-- value -- the one closest to zero -- which is why these sort descending.
-- Sorting them the intuitive way returns exactly the wrong hour, silently.
select
    'Q2a: lowest max drawdown'           as question,  -- noqa: ST06
    to_char(trade_hour, 'FM00') || ':00' as hour_utc,
    trade_count,
    round(max_drawdown_pct, 2)           as max_drawdown_pct,
    round(worst_trade_pct, 2)            as worst_trade_pct,
    round(total_return_pct, 2)           as total_return_pct
from analytics_facts.fct_btcusdt_strategy_by_hour
order by max_drawdown_pct desc
limit 1;


select
    'Q2b: shallowest single-day loss'    as question,  -- noqa: ST06
    to_char(trade_hour, 'FM00') || ':00' as hour_utc,
    trade_count,
    round(worst_trade_pct, 2)            as worst_trade_pct,
    round(max_drawdown_pct, 2)           as max_drawdown_pct,
    round(total_return_pct, 2)           as total_return_pct
from analytics_facts.fct_btcusdt_strategy_by_hour
order by worst_trade_pct desc
limit 1;


-- All 24 hours, for context. Both answers above are drawn from one sample of
-- 24 candidates, so the runners-up are worth seeing next to the winner.
select
    to_char(trade_hour, 'FM00') || ':00' as hour_utc,  -- noqa: ST06
    trade_count,
    round(total_return_pct, 2)           as total_return_pct,
    round(avg_return_pct, 4)             as avg_return_pct,
    round(max_drawdown_pct, 2)           as max_drawdown_pct,
    round(worst_trade_pct, 2)            as worst_trade_pct,
    round(best_trade_pct, 2)             as best_trade_pct,
    round(win_rate_pct, 1)               as win_rate_pct
from analytics_facts.fct_btcusdt_strategy_by_hour
order by total_return_pct desc;
