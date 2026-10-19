"""种子数据初始化脚本 — 创建默认用户和示例数据。"""

import asyncio
import uuid
from datetime import datetime

from src.api.auth.security import hash_password
from src.db.models import (
    ClauseRecord,
    ComplianceFindingRecord,
    HumanFeedbackRecord,
    ReviewRecord,
    RiskFindingRecord,
    SuggestionRecord,
    UserRecord,
)
from src.db.session import Base, async_session_factory, engine, init_db
from src.config import settings


async def create_seed_data():
    """创建种子数据。"""
    # 初始化数据库
    if settings.DATABASE_URL:
        init_db(settings.DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        print("未配置数据库，跳过种子数据创建")
        return

    async with async_session_factory() as session:
        # 检查是否已有数据
        from sqlalchemy import select, func

        count = await session.scalar(select(func.count()).select_from(UserRecord))
        if count and count > 0:
            print(f"数据库已有 {count} 个用户，跳过种子数据创建")
            return

        # 创建用户
        hashed_pwd = hash_password("admin123")
        users = [
            UserRecord(
                id="admin-001",
                tenant_id="tenant-001",
                username="admin",
                email="admin@example.com",
                hashed_password=hashed_pwd,
                role="admin",
                is_active=True,
            ),
            UserRecord(
                id="reviewer-001",
                tenant_id="tenant-001",
                username="reviewer",
                email="reviewer@example.com",
                hashed_password=hashed_pwd,
                role="reviewer",
                is_active=True,
            ),
            UserRecord(
                id="viewer-001",
                tenant_id="tenant-001",
                username="viewer",
                email="viewer@example.com",
                hashed_password=hashed_pwd,
                role="viewer",
                is_active=True,
            ),
        ]
        session.add_all(users)
        await session.flush()
        print(f"创建了 {len(users)} 个用户")

        # 创建审查记录
        review1 = ReviewRecord(
            id="rev-0001",
            tenant_id="tenant-001",
            user_id="admin-001",
            status="completed",
            document_path="/uploads/contract1.pdf",
            original_filename="技术服务合同.pdf",
            raw_text="甲方：北京科技有限公司\n乙方：上海软件开发有限公司\n第一条 服务内容\n乙方为甲方提供企业管理系统的设计、开发、测试和部署服务。\n第二条 费用及支付\n合同总金额为人民币100万元。\n第三条 违约责任\n乙方违约需支付合同总金额三倍的赔偿金。",
            contract_type="技术服务合同",
            overall_risk_level="high",
            risk_summary="检测到高风险条款：三倍赔偿金可能不合理",
            overall_compliance="needs_review",
            missing_clauses=["保密条款"],
            needs_human_review=False,
        )
        session.add(review1)
        await session.flush()

        # 创建条款
        clauses = [
            ClauseRecord(
                id="cl-0001",
                review_id=review1.id,
                title="第一条 服务内容",
                content="乙方为甲方提供企业管理系统的设计、开发、测试和部署服务。",
                category="other",
                section_number="1",
            ),
            ClauseRecord(
                id="cl-0002",
                review_id=review1.id,
                title="第二条 费用及支付",
                content="合同总金额为人民币100万元。",
                category="payment",
                section_number="2",
            ),
            ClauseRecord(
                id="cl-0003",
                review_id=review1.id,
                title="第三条 违约责任",
                content="乙方违约需支付合同总金额三倍的赔偿金。",
                category="penalty",
                section_number="3",
            ),
        ]
        session.add_all(clauses)
        await session.flush()

        # 创建风险发现
        risk_findings = [
            RiskFindingRecord(
                review_id=review1.id,
                clause_id="cl-0003",
                risk_level="high",
                risk_type="unfair_penalty",
                description="三倍赔偿金可能不合理，超出法律允许范围",
                buyer_impact="甲方需承担过高赔偿风险",
                seller_impact="乙方可能面临巨额赔偿",
                rationale="违约金条款过于严厉",
            ),
        ]
        session.add_all(risk_findings)
        await session.flush()

        # 创建合规发现
        compliance_findings = [
            ComplianceFindingRecord(
                review_id=review1.id,
                clause_id="cl-0003",
                status="non_compliant",
                regulation="《民法典》第585条",
                issue="违约金过高",
                recommendation="建议调整为合理范围",
            ),
        ]
        session.add_all(compliance_findings)
        await session.flush()

        # 创建修改建议
        suggestions = [
            SuggestionRecord(
                review_id=review1.id,
                clause_id="cl-0003",
                original_text="乙方违约需支付合同总金额三倍的赔偿金。",
                suggested_text="乙方违约需支付合同总金额30%的违约金。",
                reason="三倍赔偿金超出法律允许范围",
                priority="high",
            ),
        ]
        session.add_all(suggestions)
        await session.flush()

        # 创建第二条审查记录（待审核）
        review2 = ReviewRecord(
            id="rev-0002",
            tenant_id="tenant-001",
            user_id="admin-001",
            status="awaiting_human",
            document_path="/uploads/contract2.pdf",
            original_filename="劳动合同.pdf",
            raw_text="甲方：北京创新科技有限公司\n乙方：张三\n第一条 合同期限\n本合同有效期为2024年1月1日至2024年12月31日。\n第二条 试用期\n试用期为6个月。",
            contract_type="劳动合同",
            overall_risk_level="high",
            risk_summary="检测到高风险：试用期过长",
            overall_compliance="needs_review",
            missing_clauses=["社会保险条款", "劳动保护条款"],
            needs_human_review=True,
        )
        session.add(review2)
        await session.flush()

        # 创建第二条审查记录的条款
        clauses2 = [
            ClauseRecord(
                id="cl-0010",
                review_id=review2.id,
                title="第一条 合同期限",
                content="本合同有效期为2024年1月1日至2024年12月31日。",
                category="termination",
                section_number="1",
            ),
            ClauseRecord(
                id="cl-0011",
                review_id=review2.id,
                title="第二条 试用期",
                content="试用期为6个月。",
                category="other",
                section_number="2",
            ),
        ]
        session.add_all(clauses2)
        await session.flush()

        # 创建第二条审查记录的风险发现
        risk_findings2 = [
            RiskFindingRecord(
                review_id=review2.id,
                clause_id="cl-0011",
                risk_level="high",
                risk_type="vague_terms",
                description="试用期6个月过长，超出劳动合同法规定",
                buyer_impact="甲方可能违反劳动法",
                seller_impact="乙方权益受损",
                rationale="试用期不得超过法定上限",
            ),
        ]
        session.add_all(risk_findings2)
        await session.flush()

        await session.commit()
        print("种子数据创建完成")


if __name__ == "__main__":
    asyncio.run(create_seed_data())
