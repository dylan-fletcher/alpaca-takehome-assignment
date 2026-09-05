-- The two fact models filter independently, so a change to one that is not
-- made to the other would silently drop trades from the ranking while both
-- models still built and every column test still passed.

with ledger as (

    select count(*) as trades from {{ ref('fct_btcusdt_hourly_trades') }}

),

summary as (

    select sum(trade_count) as trades
    from {{ ref('fct_btcusdt_strategy_by_hour') }}

)

select
    ledger.trades  as ledger_trades,
    summary.trades as summary_trades
from ledger
cross join summary
where ledger.trades <> summary.trades
