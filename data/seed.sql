-- ============================================================
-- 多Agent智能合同审查系统 - PostgreSQL 种子数据
-- 密码: admin123 (bcrypt hash, rounds=12)
-- 说明：
--   配套 migrations/ 目录中的 DDL 脚本；本文件仅负责插入初始数据。
--   执行顺序：metadata.create_all → migrations/*.sql → seed.sql
--   由应用在启动时（SEED_ON_STARTUP=true 且 users 表为空）读取并执行；
--   也可用 scripts/seed_data.py 手动灌数据。
-- ============================================================

-- 1. 用户表（密码均为 admin123）
INSERT INTO users (id, tenant_id, username, email, hashed_password, role, is_active, created_at, updated_at)
VALUES
  ('admin-001',    'tenant-001', 'admin',    'admin@example.com',    '$2b$12$iWheK/VZWEx6/oANU18fq.736.a1KfJljSbOoR2EEjIZFuaDYCNvK', 'admin',    true, NOW(), NOW()),
  ('reviewer-001', 'tenant-001', 'reviewer', 'reviewer@example.com', '$2b$12$iWheK/VZWEx6/oANU18fq.736.a1KfJljSbOoR2EEjIZFuaDYCNvK', 'reviewer', true, NOW(), NOW()),
  ('viewer-001',   'tenant-001', 'viewer',   'viewer@example.com',   '$2b$12$iWheK/VZWEx6/oANU18fq.736.a1KfJljSbOoR2EEjIZFuaDYCNvK', 'viewer',   true, NOW(), NOW())
ON CONFLICT (username) DO NOTHING;

-- 2. 审查记录（含进度/版本/错误 等完整字段）
INSERT INTO reviews (
    id, tenant_id, user_id, status, document_path, original_filename, raw_text,
    contract_type, overall_risk_level, risk_summary,
    overall_compliance, missing_clauses, version_diff,
    needs_human_review, errors,
    current_step, step_log, progress,
    created_at, updated_at
) VALUES
  ('rev-0001', 'tenant-001', 'admin-001', 'completed', '/uploads/contract1.pdf', '技术服务合同.pdf',
   '甲方：北京科技有限公司
乙方：上海软件开发有限公司
第一条 服务内容
乙方为甲方提供企业管理系统的设计、开发、测试和部署服务。
第二条 费用及支付
合同总金额为人民币100万元。
第三条 违约责任
乙方违约需支付合同总金额三倍的赔偿金。',
   '技术服务合同', 'high', '检测到高风险条款：三倍赔偿金可能不合理',
   'needs_review', '["保密条款"]'::json, '',
   false, '[]'::json,
   '', '[]'::json, 1.0,
   NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

-- 3. 条款记录（category 使用 ClauseCategory 枚举值：payment/liability/other/...）
INSERT INTO clauses (id, review_id, title, content, category, section_number, entities)
VALUES
  ('cl-0001', 'rev-0001', '第一条 服务内容', '乙方为甲方提供企业管理系统的设计、开发、测试和部署服务。', 'other',     '1', '[]'::json),
  ('cl-0002', 'rev-0001', '第二条 费用及支付', '合同总金额为人民币100万元。', 'payment',   '2', '[]'::json),
  ('cl-0003', 'rev-0001', '第三条 违约责任', '乙方违约需支付合同总金额三倍的赔偿金。', 'liability', '3', '[]'::json)
ON CONFLICT (id) DO NOTHING;

-- 4. 风险发现
INSERT INTO risk_findings (review_id, clause_id, risk_level, risk_type, description, buyer_impact, seller_impact, rationale)
VALUES
  ('rev-0001', 'cl-0003', 'high', 'unfair_penalty', '三倍赔偿金可能不合理，超出法律允许范围',
   '甲方需承担过高赔偿风险', '乙方可能面临巨额赔偿', '违约金条款过于严厉');

-- 5. 合规发现
INSERT INTO compliance_findings (review_id, clause_id, status, regulation, issue, recommendation)
VALUES
  ('rev-0001', 'cl-0003', 'non_compliant', '《民法典》第585条', '违约金过高', '建议调整为合理范围');

-- 6. 修改建议
INSERT INTO suggestions (review_id, clause_id, original_text, suggested_text, reason, priority)
VALUES
  ('rev-0001', 'cl-0003', '乙方违约需支付合同总金额三倍的赔偿金。',
   '乙方违约需支付合同总金额30%的违约金。', '三倍赔偿金超出法律允许范围', 'high');

-- 7. 协商追踪会话（种子数据）
INSERT INTO negotiation_sessions (session_id, review_id, status, current_round, rounds, created_at, updated_at)
VALUES
  -- 进行中的协商会话
  ('neg-a1b2c3d4', 'rev-0001', 'active', 2,
   '[
     {
       "round_number": 1,
       "reviewer": "张经理",
       "decision": "modify",
       "comments": "违约金条款过于严厉，建议修改为更合理的比例",
       "modifications": [
         {
           "original_text": "乙方违约需支付合同总金额三倍的赔偿金",
           "new_text": "乙方违约需支付合同总金额30%的违约金",
           "reason": "三倍赔偿金超出法律允许范围"
         }
       ],
       "timestamp": "2024-01-15T10:30:00"
     },
     {
       "round_number": 2,
       "reviewer": "李法务",
       "decision": "modify",
       "comments": "修改后的比例合理，但建议增加违约金上限条款",
       "modifications": [
         {
           "original_text": "乙方违约需支付合同总金额30%的违约金",
           "new_text": "乙方违约需支付合同总金额30%的违约金，但最高不超过合同总金额的50%",
           "reason": "增加违约金上限，避免争议"
         }
       ],
       "timestamp": "2024-01-16T14:20:00"
     }
   ]'::json,
   '2024-01-15T09:00:00', '2024-01-16T14:20:00'),

  -- 已完成的协商会话
  ('neg-e5f6g7h8', 'rev-0001', 'completed', 1,
   '[
     {
       "round_number": 1,
       "reviewer": "王总监",
       "decision": "approve",
       "comments": "合同条款整体合理，同意签署",
       "modifications": [],
       "timestamp": "2024-01-10T16:00:00"
     }
   ]'::json,
   '2024-01-10T09:00:00', '2024-01-10T16:00:00'),

  -- 已拒绝的协商会话
  ('neg-i9j0k1l2', 'rev-0001', 'rejected', 1,
   '[
     {
       "round_number": 1,
       "reviewer": "赵审计",
       "decision": "reject",
       "comments": "合同中存在多项高风险条款，建议重新起草",
       "modifications": [],
       "timestamp": "2024-01-12T11:00:00"
     }
   ]'::json,
   '2024-01-12T09:00:00', '2024-01-12T11:00:00'),

  -- 新建的协商会话（无反馈记录）
  ('neg-m3n4o5p6', 'rev-0001', 'active', 0,
   '[]'::json,
   '2024-01-17T09:00:00', '2024-01-17T09:00:00')
ON CONFLICT (session_id) DO NOTHING;
