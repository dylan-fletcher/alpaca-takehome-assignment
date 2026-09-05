-- One row per hour of the day: how the strategy did if the analyst only ever
-- traded that hour. 24 rows, and the table both questions are answered from.

with trades as (

    select * from {{ ref('fct_btcusdt_hourly_trades') }}

),

aggregated as (

    -- One pass over the ledger. `growth_multiple` is the compounded result:
    -- the multipliers are multiplied, not the percentages added, because the
    -- analyst reinvests each trade's proceeds into the next. See the
    -- `product` macro for why a product needs exp/ln in Postgres.
    select
        ---------- grain
        trade_hour,
        ---------- coverage, so an uneven comparison stays visible
        count(*)                           as trade_count,
        min(trade_date)                    as first_trade_date,
        max(trade_date)                    as last_trade_date,
        ---------- question 1: which hour had the biggest returns
        {{ product('gross_return') }}      as growth_multiple,
        ---------- question 2: which hour had the lowest maximum loss
        -- A trade is flat overnight, so its percentage loss is independent
        -- of every other trade and the worst one stands alone. Nothing
        -- accumulates, so there is no drawdown curve to trace.
        min(return_pct)                    as worst_trade_pct,
        ---------- context, so neither answer has to be taken on faith
        max(return_pct)                    as best_trade_pct,
        avg(return_pct)                    as avg_return_pct,
        stddev_samp(return_pct)            as stddev_return_pct,
        avg((gross_return > 1)::int) * 100 as win_rate_pct
    from trades
    group by trade_hour

),

by_hour as (

    -- Both headline numbers are readings of the same multiple: less one and
    -- times 100 gives a percentage, times the opening stake gives a position.
    select
        ---------- grain
        trade_hour,
        ---------- coverage
        trade_count,
        first_trade_date,
        last_trade_date,
        ---------- question 1
        (growth_multiple - 1) * 100 as total_return_pct,
        {{ var('initial_units') }}
        * growth_multiple           as ending_units,
        ---------- question 2
        worst_trade_pct,
        ---------- context
        best_trade_pct,
        avg_return_pct,
        stddev_return_pct,
        win_rate_pct
    from aggregated

)

select * from by_hour
