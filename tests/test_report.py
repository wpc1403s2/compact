"""审查报告生成测试。"""

from __future__ import annotations

import pytest
from datetime import datetime

from src.pipeline.state import (
    Clause,
    ComplianceFinding,
    ContractReviewState,
    HumanFeedback,
    RiskFinding,
    ReviewStatus,
    Suggestion,
)
from src.services.report_service import generate_report, generate_word


def _make_state() -> ContractReviewState:
    """构造测试用的完整审查状态。"""
    return ContractReviewState(
        review_id="report-test-001",
        status=ReviewStatus.COMPLETED,
        created_at=datetime(2026, 7, 11, 10, 30, 0),
        raw_text="合同原文...",
        contract_type="买卖合同",
        clauses=[
            Clause(id="c001", title="付款条款", content="买方应在30日内付款", category="payment"),
            Clause(id="c002", title="违约责任", content="乙方承担无限责任", category="liability"),
        ],
        risk_findings=[
            RiskFinding(
                clause_id="c002",
                risk_level="high",
                risk_type="无限责任",
                description="违约条款构成无限责任，风险极高",
                buyer_impact="无保障",
                seller_impact="风险极高",
                rationale="违反民法典第585条",
            ),
        ],
        overall_risk_level="high",
        risk_summary="发现1个高风险条款",
        compliance_findings=[
            ComplianceFinding(
                clause_id="c002",
                status="non_compliant",
                regulation="《民法典》第585条",
                issue="违约金超出合理范围",
                recommendation="建议设置上限",
            ),
        ],
        missing_clauses=["争议解决条款"],
        overall_compliance="non_compliant",
        suggestions=[
            Suggestion(
                clause_id="c002",
                original_text="乙方承担无限责任",
                suggested_text="乙方赔偿以合同总额20%为上限",
                reason="原条款构成无限责任",
                priority="high",
            ),
        ],
        needs_human_review=True,
        human_feedback=HumanFeedback(
            reviewer="张三",
            decision="approve",
            comments="风险可控，同意签约",
            timestamp=datetime(2026, 7, 11, 14, 0, 0),
        ),
    )


class TestReportGeneration:
    def test_generate_report_returns_bytes(self):
        state = _make_state()
        pdf = generate_report(state)
        assert isinstance(pdf, bytes)
        assert len(pdf) > 0

    def test_report_starts_with_pdf_header(self):
        state = _make_state()
        pdf = generate_report(state)
        assert pdf[:5] == b"%PDF-"

    def test_report_with_empty_state(self):
        state = ContractReviewState(review_id="empty-001")
        pdf = generate_report(state)
        assert isinstance(pdf, bytes)
        assert pdf[:5] == b"%PDF-"

    def test_report_with_no_findings(self):
        state = ContractReviewState(
            review_id="clean-001",
            contract_type="服务合同",
            status=ReviewStatus.COMPLETED,
        )
        pdf = generate_report(state)
        assert len(pdf) > 0
        assert pdf[:5] == b"%PDF-"

    def test_report_with_all_risk_levels(self):
        state = _make_state()
        state.risk_findings.extend([
            RiskFinding(
                clause_id="c001", risk_level="medium", risk_type="付款风险",
                description="付款期限偏长",
            ),
            RiskFinding(
                clause_id="c001", risk_level="low", risk_type="信息不完整",
                description="缺少违约金上限说明",
            ),
        ])
        pdf = generate_report(state)
        assert len(pdf) > 0

    def test_pdf_contains_cjk_font(self):
        """Registered STSong-Light font must be referenced in the PDF (otherwise Chinese would be blank)."""
        state = _make_state()
        pdf = generate_report(state)
        assert b"STSong-Light" in pdf


class TestWordGeneration:
    def test_generate_word_returns_bytes(self):
        state = _make_state()
        docx = generate_word(state)
        assert isinstance(docx, bytes)
        assert len(docx) > 0

    def test_word_starts_with_docx_header(self):
        state = _make_state()
        docx = generate_word(state)
        # .docx is a ZIP archive — starts with PK magic
        assert docx[:2] == b"PK"

    def test_word_with_empty_state(self):
        state = ContractReviewState(review_id="empty-w-001")
        docx = generate_word(state)
        assert isinstance(docx, bytes)
        assert docx[:2] == b"PK"

    def test_word_contains_chinese_text(self):
        """After unzipping, document.xml should contain Chinese strings from our state."""
        import zipfile
        state = _make_state()
        docx = generate_word(state)
        from io import BytesIO
        with zipfile.ZipFile(BytesIO(docx)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        assert "合同审查报告" in xml
        assert "买卖合同" in xml
        assert "高风险" in xml
        assert "乙方承担无限责任" in xml
