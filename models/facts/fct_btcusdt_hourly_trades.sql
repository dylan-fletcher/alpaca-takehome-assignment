-- One row per trade: buy at the first second of an hour, sell at the last.
--
-- The ledger every answer is aggregated from. A trade opens and closes inside
-- the same hour and holds nothing overnight, so its return depends on nothing
-- outside its own row -- there are no window functions here and none are
-- needed. Compounding across trades belongs to the summary model.

with hourly_prices as (

    select * from {{ ref('int_btcusdt_hourly_prices') }}

),

tradeable_hours as (

    -- A null price means the exchange published nothing at :00:00 or :59:59,
    -- so there was no trade to make -- 22 hours, all inside the seven known
    -- outages. Dropping them is arithmetically identical to recording a 1.0
    -- multiplier, and it keeps trade_count honest about what was traded.
    --
    -- The date floor drops 2021-02-23, which begins at 09:20:20 and so offers
    -- hours 10-23 a trade that hours 00-09 never get. Ranking 24 hours over
    -- unequal date ranges would hand the later ones a free extra day.
    select
        hour_start_at,
        open_price,
        close_price
    from hourly_prices
    where open_price is not null
        and close_price is not null
        and hour_start_at >= '{{ var("backtest_start_date") }}'
        -- Both filters are jinja-conditional so the default build stays a
        -- plain scan. Note sqlfluff's `var()` stub is never none, so it lints
        -- these branches as if they were always emitted -- hence the indent.
        {%- if var("backtest_end_date") is not none %}
            and hour_start_at::date <= date '{{ var("backtest_end_date") }}'
        {%- endif %}
        {%- if var("trade_hour") is not none %}
            and extract(hour from hour_start_at) = {{ var("trade_hour") }}
        {%- endif %}

),

trades as (

    select
        ---------- grain
        hour_start_at,
        ---------- the round trip
        open_price                              as entry_price,
        close_price                             as exit_price,
        ---------- slicing keys, so "a different hour or day" needs no rebuild
        hour_start_at::date                     as trade_date,
        extract(hour from hour_start_at)::int   as trade_hour,
        extract(isodow from hour_start_at)::int as day_of_week,
        ---------- what the trade returned
        -- Two spellings of one number. `gross_return` is the multiplier the
        -- stake is scaled by: 1.007241 means the hour ended with 1.007241x
        -- what it opened with. Compounding multiplies those; it does not add
        -- the percentages, which is why both are carried.
        close_price / open_price                as gross_return,
        (close_price / open_price - 1) * 100    as return_pct
    from tradeable_hours

)

select * from trades
