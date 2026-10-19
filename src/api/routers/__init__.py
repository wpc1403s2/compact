"""API 路由模块。"""

from .auth import router as auth_router
from .health import router as health_router
from .metrics import router as metrics_router
from .review import router as review_router
from .rules import router as rules_router
from .templates import router as templates_router
from .regulations import router as regulations_router
from .negotiation import router as negotiation_router
from .diff import router as diff_router

__all__ = [
    "auth_router",
    "health_router",
    "metrics_router",
    "review_router",
    "rules_router",
    "templates_router",
    "regulations_router",
    "negotiation_router",
    "diff_router",
]
