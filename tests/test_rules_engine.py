"""规则引擎单元测试 — 无需LLM API Key即可运行。"""

import pytest

from src.rules.engine import RuleEngine, RuleSeverity


@pytest.fixture
def engine():
    return RuleEngine()


class TestRuleEngine:
    def test_unlimited_liability_detected(self, engine):
        results = engine.evaluate_clause("乙方应承担全部损失，且赔偿不设上限。")
        assert any(r.severity == RuleSeverity.CRITICAL for r in results)

    def test_unilateral_termination_detected(self, engine):
        results = engine.evaluate_clause("甲方有权单方解除合同。")
        assert any("单方解除" in r.message or r.rule_id == "R002" for r in results)

    def test_no_risk_in_normal_clause(self, engine):
        results = engine.evaluate_clause("双方应按照合同约定履行各自义务。")
        assert len(results) == 0

    def test_vague_terms_detected(self, engine):
        results = engine.evaluate_clause("具体金额由双方另行协商确定。")
        assert any(r.severity == RuleSeverity.INFO for r in results)

    def test_format_clause_invalid(self, engine):
        results = engine.evaluate_clause("本合同免除己方责任的条款。")
        critical = [r for r in results if r.severity == RuleSeverity.CRITICAL]
        assert len(critical) > 0

    def test_auto_renewal_detected(self, engine):
        results = engine.evaluate_clause("合同到期后自动续约一年。")
        assert len(results) > 0

    def test_statistics(self, engine):
        stats = engine.get_statistics()
        assert stats["total_rules"] > 0
        assert "by_severity" in stats
        assert "by_category" in stats
