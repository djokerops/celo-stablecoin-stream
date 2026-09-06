CREATE MATERIALIZED VIEW stablecoin_transfers_mv TO stablecoin_transfers AS
SELECT
   symbol, peg, tx_hash, log_index, block_number,
   toDateTime(block_timestamp) AS block_timestamp,
   from_address, to_address,
   amount_token, usd_rate, amount_usd
FROM stablecoin_transfers_kafka;