"""FastAPI 认证依赖 — Token 解析、用户获取、角色校验。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.session import get_db
from .security import decode_access_token

# 如果未配置 JWT_SECRET_KEY（开发模式），跳过认证
_auth_enabled = settings.JWT_SECRET_KEY != "dev-secret-change-in-production"

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    """当前认证用户的信息。"""
    id: str
    role: str
    tenant_id: str = ""


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser | None:
    """从 Bearer Token 中解析当前用户。

    - 未配置认证时返回 None（开发模式）
    - Token 无效时抛出 401
    """
    if not _auth_enabled:
        return None

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的 Token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    role = payload.get("role", "viewer")
    tenant_id = payload.get("tenant_id", "")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 中缺少用户信息",
        )

    return CurrentUser(id=user_id, role=role, tenant_id=tenant_id)


def require_role(*allowed_roles: str):
    """角色校验依赖工厂。

    用法：Depends(require_role("admin", "reviewer"))
    """
    async def _check(
        user: CurrentUser | None = Depends(get_current_user),
    ) -> CurrentUser | None:
        if not _auth_enabled:
            return None
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="未认证",
            )
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要 {', '.join(allowed_roles)} 角色",
            )
        return user
    return _check
