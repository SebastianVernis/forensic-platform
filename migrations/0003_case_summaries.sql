-- Case summaries: generated once per case, cached in D1
CREATE TABLE IF NOT EXISTS case_summaries (
  case_id TEXT PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
  stats_json TEXT NOT NULL,        -- statistical summary
  graph_summary TEXT NOT NULL,      -- textual graph description
  severity_report TEXT NOT NULL,    -- inconsistencies by severity
  entity_report TEXT NOT NULL,      -- top entities and relationships
  generated_at TEXT NOT NULL DEFAULT (datetime('now')),
  data_hash TEXT                    -- hash of source data to detect changes
);
