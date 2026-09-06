CREATE TABLE stablecoin_transfers
(
   symbol          LowCardinality(String),
   peg             LowCardinality(String),
   tx_hash         String,
   log_index       UInt32,
   block_number    UInt64,
   block_timestamp DateTime,
   from_address    String,
   to_address      String,
   amount_token    Float64,
   usd_rate        Float64,
   amount_usd      Float64
)
ENGINE = MergeTree
PARTITION BY toDate(block_timestamp)
ORDER BY (block_timestamp, symbol, tx_hash, log_index);