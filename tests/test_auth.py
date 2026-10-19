"""认证模块单元测试。"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from src.db.models import Base, UserRecord


# ── Fixtures ──────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


# ── 密码哈希测试 ──────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_password(self):
        hashed = hash_password("test123")
        assert hashed != "test123"
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$")

    def test_verify_correct_password(self):
        hashed = hash_password("correct_password")
        assert verify_password("correct_password", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("correct_password")
        assert verify_password("wrong_password", hashed) is False

    def test_different_hashes(self):
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2  # bcrypt 每次生成不同的盐


# ── JWT Token 测试 ──────────────────────────────────────

class TestJWTToken:
    def test_create_and_decode_token(self):
        token = create_access_token(subject="user-001", role="admin", tenant_id="tenant-abc")
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "user-001"
        assert payload["role"] == "admin"
        assert payload["tenant_id"] == "tenant-abc"
        assert "exp" in payload

    def test_decode_invalid_token(self):
        payload = decode_access_token("invalid.token.here")
        assert payload is None

    def test_decode_tampered_token(self):
        token = create_access_token(subject="user-001", role="viewer")
        # 篡改 token
        tampered = token[:-5] + "XXXXX"
        payload = decode_access_token(tampered)
        assert payload is None

    def test_token_with_custom_expiry(self):
        from datetime import timedelta
        token = create_access_token(
            subject="user-002",
            role="reviewer",
            expires_delta=timedelta(seconds=1),
        )
        payload = decode_access_token(token)
        assert payload is not None
        # Token 有效
        assert payload["sub"] == "user-002"


# ── 数据库用户测试 ──────────────────────────────────────

class TestUserRecord:
    @pytest.mark.asyncio
    async def test_create_user(self, db_session):
        user = UserRecord(
            tenant_id="tenant-001",
            username="testuser",
            email="test@example.com",
            hashed_password=hash_password("password123"),
            role="viewer",
        )
        db_session.add(user)
        await db_session.flush()

        assert user.id is not None
        assert user.tenant_id == "tenant-001"
        assert user.username == "testuser"
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_user_role_default(self, db_session):
        user = UserRecord(
            tenant_id="tenant-002",
            username="newuser",
            email="new@example.com",
            hashed_password=hash_password("pass"),
        )
        db_session.add(user)
        await db_session.flush()

        assert user.role == "viewer"
