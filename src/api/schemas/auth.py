"""认证相关请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """用户注册请求。"""
    username: str = Field(description="用户名", min_length=3, max_length=50)
    email: str = Field(default="", description="邮箱")
    password: str = Field(description="密码", min_length=6)
    role: str = Field(default="viewer", description="角色")


class LoginRequest(BaseModel):
    """用户登录请求。"""
    username: str = Field(description="用户名")
    password: str = Field(description="密码")


class TokenResponse(BaseModel):
    """Token 响应。"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="过期时间（秒）")


class UserResponse(BaseModel):
    """用户信息响应。"""
    id: str
    tenant_id: str = ""
    username: str
    email: str
    role: str
    is_active: bool
