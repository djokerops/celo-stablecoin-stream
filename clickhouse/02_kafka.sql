CREATE TABLE stablecoin_transfers_kafka
(
   symbol          String,
   peg             String,
   tx_hash         String,
   log_index       UInt32,
   block_number    UInt64,
   block_timestamp UInt64,
   from_address    String,
   to_address      String,
   amount_token    Float64,
   usd_rate        Float64,
   amount_usd      Float64
)
ENGINE = Kafka
SETTINGS
   kafka_broker_list = 'redpanda:9092',
   kafka_topic_list  = 'celo_stablecoins',
   kafka_group_name  = 'clickhouse_stablecoins',
   kafka_format      = 'JSONEachRow',
   kafka_num_consumers = 1;