"""认证路由 — 注册、登录、用户信息。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.models import UserRecord
from ...db.session import get_db
from ..auth.security import create_access_token, decode_access_token, hash_password, verify_password
from ..schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """用户注册。"""
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库不可用",
        )
    # 检查用户名是否已存在
    stmt = select(UserRecord).where(UserRecord.username == request.username)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已存在",
        )

    # 检查邮箱是否已存在
    if request.email:
        stmt = select(UserRecord).where(UserRecord.email == request.email)
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="邮箱已被注册",
            )

    # 新注册用户自动分配租户（实际场景应由管理员创建）
    tenant_id = str(uuid.uuid4())

    user = UserRecord(
        tenant_id=tenant_id,
        username=request.username,
        email=request.email or f"{request.username}@example.com",
        hashed_password=hash_password(request.password),
        role=request.role or "viewer",
    )
    db.add(user)
    await db.flush()

    return UserResponse(
        id=user.id,
        tenant_id=user.tenant_id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录，返回 JWT Token。"""
    if db is None:
        # 内存模式：使用默认管理员账号
        if request.username == "admin" and request.password == "admin123":
            token = create_access_token(
                subject="admin-001",
                role="admin",
                tenant_id="tenant-001",
            )
            return TokenResponse(
                access_token=token,
                expires_in=settings.JWT_EXPIRE_MINUTES * 60,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    stmt = select(UserRecord).where(UserRecord.username == request.username)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用",
        )

    token = create_access_token(
        subject=user.id,
        role=user.role,
        tenant_id=user.tenant_id,
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
):
    """获取当前用户信息。"""
    if not credentials:
        # 内存模式：返回默认管理员
        return UserResponse(
            id="admin-001",
            tenant_id="tenant-001",
            username="admin",
            email="admin@example.com",
            role="admin",
            is_active=True,
        )

    from ..auth.security import decode_access_token
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="无效Token")

    user_id = payload.get("sub")

    if db is None:
        return UserResponse(
            id=user_id,
            tenant_id=payload.get("tenant_id", ""),
            username=payload.get("username", "admin"),
            email=f"{payload.get('username', 'admin')}@example.com",
            role=payload.get("role", "viewer"),
            is_active=True,
        )

    stmt = select(UserRecord).where(UserRecord.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return UserResponse(
        id=user.id,
        tenant_id=user.tenant_id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
    )
