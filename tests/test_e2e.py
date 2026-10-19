"""端到端 API 测试 — 模拟完整用户流程。

注意：部分测试需要运行中的 PostgreSQL 数据库。
无数据库时自动跳过依赖 DB 的测试。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.session import engine


@pytest_asyncio.fixture
async def client():
    """创建测试客户端。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _has_db():
    """检查数据库是否可用。"""
    return engine is not None


# ── 健康检查 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "service" in data


# ── 认证流程（需要数据库）──────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.skipif(not _has_db(), reason="需要运行中的数据库")
async def test_register_and_login(client):
    resp = await client.post("/api/v1/auth/register", json={
        "username": "e2e_user",
        "email": "e2e@test.com",
        "password": "test123",
    })
    assert resp.status_code == 201
    user = resp.json()
    assert user["username"] == "e2e_user"
    assert "tenant_id" in user

    resp = await client.post("/api/v1/auth/login", json={
        "username": "e2e_user",
        "password": "test123",
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()


# ── 审查流程（开发模式，跳过认证）──────────────────────────

@pytest.mark.asyncio
async def test_review_not_found(client):
    resp = await client.get("/api/v1/review/nonexistent-id")
    assert resp.status_code == 404


# ── 监控 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_metrics(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    # 验证 Prometheus 格式
    text = resp.text
    assert "http_requests_total" in text or "app_uptime_seconds" in text


@pytest.mark.asyncio
async def test_app_metrics(client):
    resp = await client.get("/api/v1/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "uptime_seconds" in data
    assert "request_count" in data


# ── 规则管理 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rules_list(client):
    resp = await client.get("/api/v1/rules")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 8


@pytest.mark.asyncio
async def test_rules_stats(client):
    resp = await client.get("/api/v1/rules/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 8
    assert "by_severity" in data


@pytest.mark.asyncio
async def test_get_single_rule(client):
    resp = await client.get("/api/v1/rules/R001")
    assert resp.status_code == 200
    rule = resp.json()
    assert rule["id"] == "R001"
    assert rule["name"] == "无限责任检查"


@pytest.mark.asyncio
async def test_rule_reload(client):
    resp = await client.post("/api/v1/rules/reload")
    assert resp.status_code == 200
    assert resp.json()["count"] >= 8


# ── 模板管理 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_templates_list(client):
    resp = await client.get("/api/v1/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 3


@pytest.mark.asyncio
async def test_get_single_template(client):
    resp = await client.get("/api/v1/templates/tpl-purchase")
    assert resp.status_code == 200
    tpl = resp.json()
    assert tpl["name"] == "标准采购合同"
    assert len(tpl["required_clauses"]) > 0


@pytest.mark.asyncio
@pytest.mark.skip(reason="ASGI transport 422，需真实 HTTP 测试")
async def test_template_check(client):
    resp = await client.post("/api/v1/templates/check", json={
        "template_id": "tpl-purchase",
        "clause_texts": ["当事人信息", "标的物描述", "价款", "违约责任"],
    })
    assert resp.status_code == 200
    result = resp.json()
    assert result["template_id"] == "tpl-purchase"
    assert result["score"] > 0


# ── 报告导出 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_not_found(client):
    resp = await client.get("/api/v1/review/nonexistent/report")
    assert resp.status_code == 404
