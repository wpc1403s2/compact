"""密码哈希 + JWT Token 生成/验证。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from ...config import settings

# ── 密码哈希 ──────────────────────────────────────────────


def hash_password(password: str) -> str:
    """明文密码 → bcrypt 哈希。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希是否匹配。"""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


# ── JWT Token ──────────────────────────────────────────────

def create_access_token(
    subject: str,
    role: str,
    tenant_id: str = "",
    expires_delta: timedelta | None = None,
) -> str:
    """生成 JWT Access Token。

    Args:
        subject: 用户 ID
        role: 用户角色
        tenant_id: 租户 ID
        expires_delta: 过期时间增量，默认从配置读取
    """
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    payload = {
        "sub": subject,
        "role": role,
        "tenant_id": tenant_id,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """解码 JWT Token，失败返回 None。"""
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
