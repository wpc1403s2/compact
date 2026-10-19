"""协商追踪路由 — 协商会话管理、反馈提交。"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/negotiation", tags=["negotiation"])

# 内存存储（生产环境应使用数据库）
_sessions: dict[str, dict] = {}

# 初始化种子数据
def _init_seed_data():
    """初始化协商追踪种子数据。"""
    _sessions.update({
        "neg-a1b2c3d4": {
            "session_id": "neg-a1b2c3d4",
            "review_id": "rev-0001",
            "status": "active",
            "current_round": 2,
            "rounds": [
                {
                    "round_number": 1,
                    "reviewer": "张经理",
                    "decision": "modify",
                    "comments": "违约金条款过于严厉，建议修改为更合理的比例",
                    "modifications": [
                        {
                            "original_text": "乙方违约需支付合同总金额三倍的赔偿金",
                            "new_text": "乙方违约需支付合同总金额30%的违约金",
                            "reason": "三倍赔偿金超出法律允许范围"
                        }
                    ],
                    "timestamp": "2024-01-15T10:30:00"
                },
                {
                    "round_number": 2,
                    "reviewer": "李法务",
                    "decision": "modify",
                    "comments": "修改后的比例合理，但建议增加违约金上限条款",
                    "modifications": [
                        {
                            "original_text": "乙方违约需支付合同总金额30%的违约金",
                            "new_text": "乙方违约需支付合同总金额30%的违约金，但最高不超过合同总金额的50%",
                            "reason": "增加违约金上限，避免争议"
                        }
                    ],
                    "timestamp": "2024-01-16T14:20:00"
                }
            ],
            "created_at": "2024-01-15T09:00:00",
            "updated_at": "2024-01-16T14:20:00"
        },
        "neg-e5f6g7h8": {
            "session_id": "neg-e5f6g7h8",
            "review_id": "rev-0001",
            "status": "completed",
            "current_round": 1,
            "rounds": [
                {
                    "round_number": 1,
                    "reviewer": "王总监",
                    "decision": "approve",
                    "comments": "合同条款整体合理，同意签署",
                    "modifications": [],
                    "timestamp": "2024-01-10T16:00:00"
                }
            ],
            "created_at": "2024-01-10T09:00:00",
            "updated_at": "2024-01-10T16:00:00"
        },
        "neg-i9j0k1l2": {
            "session_id": "neg-i9j0k1l2",
            "review_id": "rev-0001",
            "status": "rejected",
            "current_round": 1,
            "rounds": [
                {
                    "round_number": 1,
                    "reviewer": "赵审计",
                    "decision": "reject",
                    "comments": "合同中存在多项高风险条款，建议重新起草",
                    "modifications": [],
                    "timestamp": "2024-01-12T11:00:00"
                }
            ],
            "created_at": "2024-01-12T09:00:00",
            "updated_at": "2024-01-12T11:00:00"
        },
        "neg-m3n4o5p6": {
            "session_id": "neg-m3n4o5p6",
            "review_id": "rev-0001",
            "status": "active",
            "current_round": 0,
            "rounds": [],
            "created_at": "2024-01-17T09:00:00",
            "updated_at": "2024-01-17T09:00:00"
        }
    })

# 模块加载时初始化种子数据
_init_seed_data()


class FeedbackRequest(BaseModel):
    reviewer: str
    decision: str  # approve/reject/modify
    comments: str = ""
    modifications: list[dict] = []


@router.post("/start")
async def start_session(review_id: str):
    """开始协商会话。"""
    session_id = f"neg-{uuid.uuid4().hex[:8]}"
    session = {
        "session_id": session_id,
        "review_id": review_id,
        "status": "active",
        "rounds": [],
        "current_round": 0,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    _sessions[session_id] = session
    return session


@router.get("/list")
async def list_sessions():
    """列出所有协商会话。"""
    return {"sessions": list(_sessions.values()), "total": len(_sessions)}


@router.get("/stats")
async def get_stats():
    """获取协商统计。"""
    active = sum(1 for s in _sessions.values() if s["status"] == "active")
    completed = sum(1 for s in _sessions.values() if s["status"] == "completed")
    rejected = sum(1 for s in _sessions.values() if s["status"] == "rejected")
    total_rounds = sum(s["current_round"] for s in _sessions.values())
    return {
        "total_sessions": len(_sessions),
        "active_sessions": active,
        "completed": completed,
        "rejected": rejected,
        "total_rounds": total_rounds,
    }


@router.get("/{session_id}")
async def get_session(session_id: str):
    """获取协商会话详情。"""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


@router.post("/{session_id}/feedback")
async def submit_feedback(session_id: str, feedback_body: FeedbackRequest):
    """提交反馈。"""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["status"] != "active":
        raise HTTPException(status_code=400, detail="会话已结束")

    round_num = session["current_round"] + 1
    round_data = {
        "round_number": round_num,
        "reviewer": feedback_body.reviewer,
        "decision": feedback_body.decision,
        "comments": feedback_body.comments,
        "modifications": feedback_body.modifications,
        "timestamp": datetime.now().isoformat(),
    }
    session["rounds"].append(round_data)
    session["current_round"] = round_num
    session["updated_at"] = datetime.now().isoformat()

    if feedback_body.decision == "approve":
        session["status"] = "completed"
    elif feedback_body.decision == "reject":
        session["status"] = "rejected"

    return {"message": "反馈提交成功"}


@router.post("/{session_id}/complete")
async def complete_session(session_id: str):
    """完成协商。"""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    session["status"] = "completed"
    session["updated_at"] = datetime.now().isoformat()
    return {"message": "协商已完成"}


@router.post("/{session_id}/reject")
async def reject_session(session_id: str, reason: str = ""):
    """拒绝协商。"""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    session["status"] = "rejected"
    session["updated_at"] = datetime.now().isoformat()
    return {"message": "协商已拒绝"}
