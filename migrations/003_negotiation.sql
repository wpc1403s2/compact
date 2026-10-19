-- 协商追踪表结构
CREATE TABLE IF NOT EXISTS negotiation_sessions (
    session_id VARCHAR(36) PRIMARY KEY,
    review_id VARCHAR(36) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    current_round INTEGER NOT NULL DEFAULT 0,
    rounds JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_negotiation_sessions_review_id ON negotiation_sessions(review_id);
CREATE INDEX IF NOT EXISTS idx_negotiation_sessions_status ON negotiation_sessions(status);
