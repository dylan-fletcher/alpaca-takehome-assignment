-- One row per hour, holding only the two prices the strategy transacts at.
--
-- The analyst buys at the first second of an hour and sells at the last, so
-- the whole 3,600-second window collapses to two numbers: the open of the
-- :00:00 second and the close of the :59:59 second. Nothing else in the hour
-- affects the backtest, so nothing else is carried forward.

with seconds as (

    select * from {{ ref('stg_binance__btcusdt_1s_ohlcv') }}

),

boundary_seconds as (

    -- Two rows per hour out of 3,600. The first and last second of an hour
    -- are calendar constants, not something to rank for, so this is a plain
    -- filter -- no window function, and no need for a QUALIFY that Postgres
    -- does not have anyway.
    select
        open_time,
        open_price,
        close_price,
        date_trunc('hour', open_time) as hour_start_at
    from seconds
    where open_time in (
            date_trunc('hour', open_time),
            date_trunc('hour', open_time)
            + interval '59 minutes 59 seconds'
        )

),

hourly as (

    -- `filter` is an aggregate clause, not a window: each one matches exactly
    -- one of the two rows in the group, so the aggregate just returns that
    -- row's value. Null means the exchange had no data at that second, which
    -- is precisely an hour the strategy could not have traded -- 22 hours out
    -- of 30,759, all inside the seven known outages. See README.md.
    select
        ---------- grain
        hour_start_at,
        ---------- the two prices the strategy transacts at
        max(open_price) filter (
            where open_time = hour_start_at
        ) as open_price,
        max(close_price) filter (
            where open_time <> hour_start_at
        ) as close_price
    from boundary_seconds
    group by hour_start_at

)

select * from hourly
