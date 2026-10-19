-- 001: original_filename 字段（记录上传文件原名）
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS original_filename VARCHAR(255) DEFAULT '';
