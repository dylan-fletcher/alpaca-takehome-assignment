-- One row per hour of the day: how the strategy did if the analyst only ever
-- traded that hour. 24 rows, and the table both questions are answered from.

with trades as (

    select * from {{ ref('fct_btcusdt_hourly_trades') }}

),

equity_curve as (

    -- What the stake is worth after each trade, as a multiple of where it
    -- started. Reinvesting is what makes this a curve rather than a list:
    -- each trade scales whatever the last one left behind.
    select
        ---------- grain
        trade_hour,
        trade_date,
        ---------- carried through for the aggregation below
        gross_return,
        return_pct,
        ---------- the compounded stake after this trade
        {{ running_product('gross_return', 'trade_hour', 'trade_date') }}
            as equity
    from trades

),

drawdown_curve as (

    -- How far below its own high-water mark the stake sits after each trade,
    -- as a negative fraction. Zero means this trade set a new high.
    --
    -- `greatest(..., 1)` seeds the peak with the opening stake. Without it an
    -- hour that loses money before ever printing a new high would measure its
    -- drawdown from the dip rather than from par, understating it -- hour 04
    -- reads -13.59% that way against a true -14.02%.
    select
        ---------- grain
        trade_hour,
        trade_date,
        ---------- carried through for the aggregation below
        gross_return,
        return_pct,
        ---------- distance below the high-water mark
        equity / greatest(
            max(equity) over (
                partition by trade_hour order by trade_date
            ), 1
        ) - 1 as drawdown
    from equity_curve

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
        ---------- question 2: two readings of "maximum loss", see _facts.yml
        -- The worst the account ever got, and the worst any single day got.
        -- They are different questions and here they name different hours.
        min(drawdown) * 100                as max_drawdown_pct,
        min(return_pct)                    as worst_trade_pct,
        ---------- context, so no answer has to be taken on faith
        max(return_pct)                    as best_trade_pct,
        avg(return_pct)                    as avg_return_pct,
        stddev_samp(return_pct)            as stddev_return_pct,
        avg((gross_return > 1)::int) * 100 as win_rate_pct
    from drawdown_curve
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
        max_drawdown_pct,
        worst_trade_pct,
        ---------- context
        best_trade_pct,
        avg_return_pct,
        stddev_return_pct,
        win_rate_pct
    from aggregated

)

select * from by_hour
