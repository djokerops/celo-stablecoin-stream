CREATE TABLE stablecoin_transfers_kafka
(
   symbol  String,
   peg String,
   tx_type String,
   tx_hash String,
   log_index UInt32,
   block_number UInt64,
   block_timestamp UInt64,
   from_address String,
   to_address  String,
   amount_token Float64,
   usd_rate Float64,
   amount_usd Float64
)
ENGINE = Kafka
SETTINGS
   kafka_broker_list = 'redpanda:9092',
   kafka_topic_list  = 'celo_stablecoins',
   kafka_group_name  = 'clickhouse_stablecoins',
   kafka_format      = 'JSONEachRow',
   kafka_num_consumers = 1,

   -- Batching. Without these the engine flushed tiny blocks continuously: the
   -- 2026-09-14 outage showed ~78,800 block numbers in a single day - roughly
   -- 100 parts a minute - merged all the way to level 16, which is what held
   -- CPU at 100% for ten minutes at a stretch and pushed baseline memory to
   -- 2.9 GiB before a single query ran. One flush every 15s (or 1M rows,
   -- whichever comes first) turns that into ~4 larger parts a minute, which
   -- the merge tree handles comfortably.
   -- The cost is latency: a transfer can now sit up to 15s in Kafka before it
   -- is queryable. The board refreshes on 30s, so this is invisible there.
   kafka_max_block_size = 1048576,
   kafka_flush_interval_ms = 15000,
   kafka_poll_max_batch_size = 65536;