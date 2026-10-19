-- 002: 进度追踪字段（current_step / step_log / progress）
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS current_step VARCHAR(50) DEFAULT '';
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS step_log JSON DEFAULT '[]';
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS progress FLOAT DEFAULT 0.0;
