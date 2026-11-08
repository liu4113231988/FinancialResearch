-- 预测台账：只记录实盘最新收盘的预测，历史回看不写入。
-- 回滚：DROP TABLE prediction_records（台账可重算，但会丢失已结算标签）。
CREATE TABLE IF NOT EXISTS prediction_records (
  id VARCHAR PRIMARY KEY,
  created_at DOUBLE NOT NULL,
  as_of VARCHAR NOT NULL,
  asset_kind VARCHAR NOT NULL,
  asset_code VARCHAR NOT NULL,
  asset_name VARCHAR,
  source VARCHAR NOT NULL,
  direction VARCHAR NOT NULL,
  horizon INTEGER NOT NULL,
  probability DOUBLE,
  rule_score DOUBLE,
  rule_action VARCHAR,
  price_basis VARCHAR,
  regime VARCHAR,
  features VARCHAR,
  model_version VARCHAR,
  params VARCHAR,
  data_stamp VARCHAR,
  status VARCHAR NOT NULL DEFAULT 'pending',
  target_date VARCHAR,
  judge_date VARCHAR,
  label VARCHAR,
  realized_return DOUBLE,
  judged_at DOUBLE,
  reason VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_prediction_records_status
  ON prediction_records(status, as_of);
