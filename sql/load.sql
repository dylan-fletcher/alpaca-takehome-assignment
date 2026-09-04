-- Landing-table load for the Binance BTCUSDT 1-second dataset.
--
-- Invoked by load.sh as:
--   psql -v ON_ERROR_STOP=1 -v csv_path=/data/half2_BTCUSDT_1s.csv -f /sql/load.sql
--
-- The path is passed in rather than hardcoded so the same file works against a
-- different mount point (or a smaller fixture) without editing SQL.

\set ON_ERROR_STOP on

CREATE SCHEMA IF NOT EXISTS raw;

DROP TABLE IF EXISTS raw.btc_1s;

-- UNLOGGED on purpose: this is a reproducible landing table in a throwaway
-- container, and skipping WAL roughly halves the load time for 13.6 GB.
-- It survives a normal restart; an unclean shutdown truncates it, in which case
-- `./load.sh --reload` rebuilds it from the CSV.
CREATE UNLOGGED TABLE raw.btc_1s (
    open_time                      timestamp     NOT NULL,
    open                           numeric(20,8),
    high                           numeric(20,8),
    low                            numeric(20,8),
    close                          numeric(20,8),
    volume                         numeric(30,8),
    close_time                     timestamp,
    quote_asset_volume             numeric(30,8),
    number_of_trades               integer,
    taker_buy_base_asset_volume    numeric(30,8),
    taker_buy_quote_asset_volume   numeric(30,8),
    ignore                         smallint
);

-- Server-side COPY: the backend reads the bind-mounted file directly rather
-- than streaming 13.6 GB through the client connection.
COPY raw.btc_1s FROM :'csv_path' WITH (FORMAT csv, HEADER true);

-- Index built after the load, not before -- maintaining it during COPY is the
-- single most expensive thing you can do to a bulk insert.
-- Deliberately not a UNIQUE index / PK: whether open_time is unique across this
-- file is an assertion about the data, and it belongs in a dbt test where a
-- violation is a readable test failure rather than a mid-load crash.
CREATE INDEX btc_1s_open_time_idx ON raw.btc_1s (open_time);

ANALYZE raw.btc_1s;
