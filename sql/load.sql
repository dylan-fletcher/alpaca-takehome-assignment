-- Landing-table load for the Binance BTCUSDT 1-second dataset.
--
-- Run by run.py, and equivalently by hand inside the container:
--   psql -v ON_ERROR_STOP=1 -v csv_path=/data/dataset.csv -f /sql/load.sql
--
-- csv_path is passed in, not hardcoded, so the same file works against a
-- fixture or a different mount point without editing SQL.

\set ON_ERROR_STOP on

create schema if not exists raw;

drop table if exists raw.btc_1s;

-- Unlogged on purpose: a reproducible landing table in a throwaway container,
-- and skipping WAL roughly halves the load time for 13.6 GB. An unclean
-- shutdown truncates it; `./run.sh --reload` rebuilds it from the CSV.
create unlogged table raw.btc_1s (
    open_time timestamp not null,
    open numeric(20, 8),
    high numeric(20, 8),
    low numeric(20, 8),
    close numeric(20, 8),
    volume numeric(30, 8),
    close_time timestamp,
    quote_asset_volume numeric(30, 8),
    number_of_trades integer,
    taker_buy_base_asset_volume numeric(30, 8),
    taker_buy_quote_asset_volume numeric(30, 8),
    ignore smallint
);

-- Server-side COPY: the backend reads the bind-mounted file directly instead
-- of streaming 13.6 GB through the client connection.
copy raw.btc_1s from :'csv_path' with (format csv, header true);

-- Built after the load: maintaining an index during COPY is the single most
-- expensive thing you can do to a bulk insert.
--
-- Not unique, and not a PK. Whether open_time is unique is an assertion about
-- the data, so it belongs in a dbt test where a violation reads as a test
-- failure instead of a mid-load crash.
create index btc_1s_open_time_idx on raw.btc_1s (open_time);

analyze raw.btc_1s;
