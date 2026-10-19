"""Celery 应用实例配置。"""

from __future__ import annotations

from celery import Celery

from ..config import settings

celery_app = Celery(
    "contract_review",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    # 序列化
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # 时区
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务配置
    task_track_started=True,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    # 结果过期时间（1 小时）
    result_expires=3600,
    # Worker 配置
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
)

# 自动发现 tasks
celery_app.autodiscover_tasks(["src.worker"])
