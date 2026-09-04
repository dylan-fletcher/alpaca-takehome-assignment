-- One row per second of BTCUSDT trading, cleaned but not reshaped.
--
-- Staging stays 1:1 with the source in grain and column set: deduplication,
-- renaming and dropping the unused `ignore` column only. No joins, no
-- aggregation -- rolling these seconds up to hours is the intermediate
-- layer's job.

with source as (

    select * from {{ source('binance', 'btc_1s') }}

),

deduplicated as (

    -- 56 timestamps in this file carry byte-identical duplicate rows -- 308
    -- rows collapsing to 56, so 252 spurious rows in all. They sit in seven
    -- 8-second seams (one per affected day) with copy counts descending 9..2,
    -- the signature of overlapping archive windows in the upstream merge.
    --
    -- Because every copy is identical across all 12 columns, keeping an
    -- arbitrary one loses no information. Leaving them in would inflate hourly
    -- traded volume by up to 20.5% in the seven affected hours.
    --
    -- `distinct on` is Postgres-specific, but it can walk the existing
    -- open_time index rather than sorting 110M rows, which a `distinct` over
    -- the full column list could not.
    select distinct on (open_time) *
    from source
    order by open_time

),

renamed as (

    select
        ---------- grain
        -- Binance timestamps are UTC. Deliberately left as naive `timestamp`
        -- rather than cast to `timestamptz`: the backtest buckets by hour, and
        -- extracting an hour from a timestamptz would silently depend on the
        -- session TimeZone setting. Naive UTC keeps that deterministic.
        open_time                       as bar_open_at,
        close_time                      as bar_close_at,

        ---------- prices (USDT)
        -- `open` and `close` are renamed partly for clarity and partly because
        -- both are reserved words in ANSI SQL.
        open                            as open_price,
        high                            as high_price,
        low                             as low_price,
        close                           as close_price,

        ---------- volumes
        volume                          as base_volume,
        quote_asset_volume              as quote_volume,
        taker_buy_base_asset_volume     as taker_buy_base_volume,
        taker_buy_quote_asset_volume    as taker_buy_quote_volume,

        ---------- counts
        number_of_trades                as trade_count

    from deduplicated

)

select * from renamed
