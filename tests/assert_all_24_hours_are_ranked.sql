-- Both questions are "which hour", so a missing hour is a wrong answer rather
-- than a smaller one -- and it would be invisible, since the query still
-- returns a plausible winner from the hours that survived. Fails loudly if
-- the summary is not exactly the 24 hours of the day, once each.
--
-- Disabled when `trade_hour` is set: the analyst has then asked for a single
-- hour, so 23 missing ones are the point rather than a defect.
{{ config(enabled = var('trade_hour') is none) }}

with ranked as (

    select * from {{ ref('fct_btcusdt_strategy_by_hour') }}

),

expected as (

    select generate_series(0, 23) as trade_hour

),

compared as (

    select
        coalesce(expected.trade_hour, ranked.trade_hour) as trade_hour,
        count(ranked.trade_hour)                         as rows_found
    from expected
    full outer join ranked
        on expected.trade_hour = ranked.trade_hour
    group by coalesce(expected.trade_hour, ranked.trade_hour)

)

select * from compared
where rows_found <> 1
