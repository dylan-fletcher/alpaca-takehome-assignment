-- One row per second of BTCUSDT trading, cleaned but not reshaped.
--
-- Staging stays 1:1 with the source in grain and column set: deduplicate,
-- rename, drop the unused `ignore` column. Rolling seconds up to hours is
-- the intermediate layer's job.

with source as (

    select * from {{ source('binance', 'btc_1s') }}

),

deduplicated as (

    -- 56 timestamps carry byte-identical duplicates (308 rows -> 56), in
    -- seven seams from an upstream archive merge. Keeping an arbitrary copy
    -- loses nothing; keeping all of them would inflate hourly volume by up
    -- to 20.5% in the affected hours. See README.md for the full breakdown.
    --
    -- `distinct on` is Postgres-only, but it walks the open_time index
    -- instead of sorting 110M rows.
    select distinct on (open_time) *
    from source
    order by open_time

),

renamed as (

    select
        ---------- grain
        -- Naive `timestamp`, not `timestamptz`: the data is UTC, and casting
        -- would make `extract(hour from ...)` depend on the session TimeZone.
        open_time                    as bar_open_at,
        close_time                   as bar_close_at,
        ---------- prices (USDT)
        -- `open` and `close` are renamed for clarity, and because both are
        -- reserved words in ANSI SQL.
        open                         as open_price,
        high                         as high_price,
        low                          as low_price,
        close                        as close_price,
        ---------- volumes
        volume                       as base_volume,
        quote_asset_volume           as quote_volume,
        taker_buy_base_asset_volume  as taker_buy_base_volume,
        taker_buy_quote_asset_volume as taker_buy_quote_volume,
        ---------- counts
        number_of_trades             as trade_count
    from deduplicated

)

select * from renamed
