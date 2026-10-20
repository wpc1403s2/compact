"""
LangGraph流水线编排 — 四Agent流水线 + 人机协同。

核心设计：
1. 使用LangGraph StateGraph构建有向图
2. 条款提取后并发执行风险识别与合规检查，再生成修改建议
3. 人机协同通过interrupt实现
4. 支持checkpoint持久化，可从任意步骤恢复
5. 条件路由：根据风险等级决定是否需要人工审核
6. 进度回调：每步开始/结束时通知外部，用于实时进度展示

面试亮点：
- LangGraph StateGraph vs MessageGraph 的区别
- interrupt/resume机制的实现原理
- checkpoint的序列化和反序列化
- 条件边(conditional edge)的使用场景
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from time import perf_counter
from typing import Any, Awaitable, Callable, Literal

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from ..agents.clause_extraction import ClauseExtractionAgent
from ..agents.risk_identification import RiskIdentificationAgent
from ..agents.compliance_check import ComplianceCheckAgent
from ..agents.suggestion import SuggestionAgent
from .state import (
    ContractReviewState,
    PIPELINE_STEPS,
    ReviewStatus,
    RiskLevel,
    STEP_LABELS,
    StepEvent,
)

logger = logging.getLogger(__name__)

# 按 review_id 注册的进度回调表（支持并发流水线互不干扰）。
# 签名：(state) -> Awaitable[None]
_progress_callbacks: dict[str, Callable[[ContractReviewState], Awaitable[None]]] = {}


def set_progress_callback(
    review_id: str,
    cb: Callable[[ContractReviewState], Awaitable[None]] | None,
) -> None:
    """注册/注销某个 review_id 的进度回调。"""
    if cb is None:
        _progress_callbacks.pop(review_id, None)
    else:
        _progress_callbacks[review_id] = cb


def _fire_progress(state: ContractReviewState) -> Awaitable[None] | None:
    cb = _progress_callbacks.get(state.review_id)
    if cb is not None:
        return cb(state)
    return None


def _now() -> datetime:
    return datetime.now()


def _step_message(state: ContractReviewState, step_key: str) -> str:
    """根据当前 state 生成步骤完成时的可读消息。"""
    if step_key == "clause_extraction":
        return f"提取了 {len(state.clauses)} 条条款"
    if step_key == "risk_identification":
        n = len(state.risk_findings)
        high = sum(1 for r in state.risk_findings if r.risk_level == RiskLevel.HIGH)
        return f"发现 {n} 个风险（高风险 {high} 个）"
    if step_key == "compliance_check":
        n = len(state.compliance_findings)
        m = len(state.missing_clauses)
        return f"合规检查完成，{n} 项不合规，缺失 {m} 项必备条款"
    if step_key == "suggestion":
        return f"生成了 {len(state.suggestions)} 条修改建议"
    return STEP_LABELS.get(step_key, step_key) + "完成"


def _with_progress(step_key: str, agent: Any) -> Callable[..., Awaitable[dict]]:
    """高阶函数：将 Agent 节点包一层进度事件记录。

    - 进入节点时：追加 running 事件，更新 current_step，触发回调。
    - 节点成功返回后：标记事件为 done，推进 progress，触发回调。
    - 异常时：标记事件为 error，追加到 state.errors，触发回调后重抛。
    """
    label = STEP_LABELS.get(step_key, step_key)

    async def _wrapped(state: ContractReviewState) -> dict:
        started_at = perf_counter()
        event = StepEvent(
            step=step_key,
            status="running",
            message=f"{label}中...",
            started_at=_now(),
        )
        state.step_log.append(event)
        state.current_step = step_key
        coro = _fire_progress(state)
        if coro is not None:
            try:
                await coro
            except Exception:
                logger.exception("进度回调异常 step=%s", step_key)

        try:
            result = await agent.run(state)
        except Exception as e:
            elapsed = perf_counter() - started_at
            event.status = "error"
            event.finished_at = _now()
            event.message = f"{label}失败: {e}"
            state.errors.append(f"{step_key}: {e}")
            logger.exception("流水线步骤失败 step=%s elapsed=%.2fs", step_key, elapsed)
            coro = _fire_progress(state)
            if coro is not None:
                try:
                    await coro
                except Exception:
                    logger.exception("进度回调异常 step=%s", step_key)
            raise

        # agent 返回部分更新字典，需要先应用到 state 以拿到最新字段计数
        for k, v in (result or {}).items():
            if hasattr(state, k):
                try:
                    setattr(state, k, v)
                except Exception:
                    pass

        event.status = "done"
        event.finished_at = _now()
        event.message = _step_message(state, step_key)
        elapsed = perf_counter() - started_at
        logger.info("流水线步骤完成 step=%s elapsed=%.2fs", step_key, elapsed)
        try:
            idx = PIPELINE_STEPS.index(step_key)
            state.progress = round((idx + 1) / len(PIPELINE_STEPS), 3)
        except ValueError:
            pass
        state.current_step = ""
        coro = _fire_progress(state)
        if coro is not None:
            try:
                await coro
            except Exception:
                logger.exception("进度回调异常 step=%s", step_key)

        updates = dict(result or {})
        updates.update(
            step_log=state.step_log,
            current_step=state.current_step,
            progress=state.progress,
            errors=state.errors,
        )
        return updates

    _wrapped.__name__ = f"wrapped_{step_key}"
    return _wrapped


def _with_parallel_progress(
    step_keys: tuple[str, str],
    agents: tuple[Any, Any],
) -> Callable[..., Awaitable[dict]]:
    """并发运行互不依赖的 Agent，并统一更新共享进度状态。"""

    async def _wrapped(state: ContractReviewState) -> dict:
        started_at = perf_counter()
        events = {
            key: StepEvent(
                step=key,
                status="running",
                message=f"{STEP_LABELS[key]}中...",
                started_at=_now(),
            )
            for key in step_keys
        }
        state.step_log.extend(events.values())
        state.current_step = "+".join(step_keys)
        coro = _fire_progress(state)
        if coro is not None:
            try:
                await coro
            except Exception:
                logger.exception("进度回调异常 steps=%s", state.current_step)

        results = await asyncio.gather(
            *(agent.run(state.model_copy(deep=True)) for agent in agents),
            return_exceptions=True,
        )
        updates: dict[str, Any] = {}
        errors: list[str] = []
        failures: list[BaseException] = []

        for step_key, result in zip(step_keys, results):
            event = events[step_key]
            event.finished_at = _now()
            if isinstance(result, BaseException):
                event.status = "error"
                event.message = f"{STEP_LABELS[step_key]}失败: {result}"
                failures.append(result)
                errors.append(f"{step_key}: {result}")
                continue

            result = result or {}
            for field, value in result.items():
                if field == "errors":
                    errors.extend(error for error in value if error not in errors)
                elif field in updates and updates[field] != value:
                    raise RuntimeError(f"并行步骤返回了冲突字段: {field}")
                else:
                    updates[field] = value
            event.status = "done"
            event.message = _step_message(
                state.model_copy(update=updates),
                step_key,
            )

        if errors:
            updates["errors"] = list(dict.fromkeys([*state.errors, *errors]))

        if failures:
            failed_steps = ", ".join(
                key for key in step_keys if events[key].status == "error"
            )
            state.errors = updates.get("errors", state.errors)
            state.current_step = ""
            elapsed = perf_counter() - started_at
            logger.error("并行步骤失败 steps=%s elapsed=%.2fs", failed_steps, elapsed)
            coro = _fire_progress(state)
            if coro is not None:
                try:
                    await coro
                except Exception:
                    logger.exception("进度回调异常 steps=%s", failed_steps)
            raise RuntimeError(f"并行步骤失败: {failed_steps}") from failures[0]

        for field, value in updates.items():
            if hasattr(state, field):
                setattr(state, field, value)

        completed_index = max(PIPELINE_STEPS.index(key) for key in step_keys)
        state.progress = round((completed_index + 1) / len(PIPELINE_STEPS), 3)
        state.current_step = ""
        updates.update(
            step_log=state.step_log,
            current_step=state.current_step,
            progress=state.progress,
            errors=state.errors,
        )
        elapsed = perf_counter() - started_at
        logger.info(
            "并行步骤完成 steps=%s elapsed=%.2fs",
            ",".join(step_keys),
            elapsed,
        )
        coro = _fire_progress(state)
        if coro is not None:
            try:
                await coro
            except Exception:
                logger.exception("进度回调异常 steps=%s", ",".join(step_keys))

        return updates

    _wrapped.__name__ = "wrapped_parallel_analysis"
    return _wrapped


def _complete_with_progress(node: Callable) -> Callable[..., Awaitable[dict]]:
    """包装 complete 节点：完成时把 progress 置 1.0 并触发回调。"""
    async def _wrapped(state: ContractReviewState) -> dict:
        out = node(state)
        if hasattr(out, "__await__"):
            out = await out
        state.progress = 1.0
        state.current_step = ""
        coro = _fire_progress(state)
        if coro is not None:
            try:
                await coro
            except Exception:
                logger.exception("进度回调异常 step=complete")
        return out or {}
    _wrapped.__name__ = "wrapped_complete"
    return _wrapped


def should_human_review(state: ContractReviewState) -> Literal["human_review", "complete"]:
    """条件路由：判断是否需要人工审核。"""
    if state.needs_human_review:
        return "human_review"
    return "complete"


def human_review_node(state: ContractReviewState) -> dict:
    """
    人机协同节点 — 暂停流水线等待人工审核。

    在实际运行中，这个节点会通过LangGraph的interrupt机制暂停。
    前端通过API提交人工审核结果后，流水线从此处恢复继续执行。
    """
    logger.info("进入人工审核节点 review_id=%s", state.review_id)
    return {
        "status": ReviewStatus.AWAITING_HUMAN,
    }


def process_human_feedback(state: ContractReviewState) -> dict:
    """处理人工反馈后的后续逻辑。"""
    feedback = state.human_feedback
    if feedback is None:
        return {"status": ReviewStatus.COMPLETED}

    if feedback.decision == "approve":
        return {"status": ReviewStatus.APPROVED}
    elif feedback.decision == "reject":
        return {"status": ReviewStatus.REJECTED}
    else:
        if feedback.modified_suggestions:
            return {
                "suggestions": feedback.modified_suggestions,
                "status": ReviewStatus.COMPLETED,
            }
        return {"status": ReviewStatus.COMPLETED}


def complete_node(state: ContractReviewState) -> dict:
    """流水线完成节点。"""
    logger.info(
        "审查完成 review_id=%s 条款=%d 风险=%d 合规=%d 建议=%d",
        state.review_id,
        len(state.clauses),
        len(state.risk_findings),
        len(state.compliance_findings),
        len(state.suggestions),
    )
    return {"status": ReviewStatus.COMPLETED}


def build_review_graph(
    with_human_review: bool = True,
    rag_retriever=None,
) -> StateGraph:
    """
    构建合同审查流水线的LangGraph有向图。

    流程：
    clause_extraction → (risk_identification + compliance_check)
        → suggestion → [human_review | complete] → END

    Args:
        with_human_review: 是否启用人机协同节点
        rag_retriever: 可选的 RAG 检索器，用于增强 Agent 上下文

    Returns:
        编译后的StateGraph
    """

    clause_agent = ClauseExtractionAgent()
    risk_agent = RiskIdentificationAgent(rag_retriever=rag_retriever)
    compliance_agent = ComplianceCheckAgent(rag_retriever=rag_retriever)
    suggestion_agent = SuggestionAgent(rag_retriever=rag_retriever)

    graph = StateGraph(ContractReviewState)

    graph.add_node("clause_extraction", _with_progress("clause_extraction", clause_agent))
    graph.add_node(
        "risk_compliance_analysis",
        _with_parallel_progress(
            ("risk_identification", "compliance_check"),
            (risk_agent, compliance_agent),
        ),
    )
    graph.add_node("suggestion", _with_progress("suggestion", suggestion_agent))
    graph.add_node("complete", _complete_with_progress(complete_node))

    if with_human_review:
        graph.add_node("human_review", human_review_node)
        graph.add_node("process_feedback", process_human_feedback)

    graph.set_entry_point("clause_extraction")
    graph.add_edge("clause_extraction", "risk_compliance_analysis")
    graph.add_edge("risk_compliance_analysis", "suggestion")

    if with_human_review:
        graph.add_conditional_edges(
            "suggestion",
            should_human_review,
            {
                "human_review": "human_review",
                "complete": "complete",
            },
        )
        graph.add_edge("human_review", "process_feedback")
        graph.add_edge("process_feedback", "complete")
    else:
        graph.add_edge("suggestion", "complete")

    graph.add_edge("complete", END)

    return graph


def create_review_pipeline(
    with_human_review: bool = True,
    with_checkpointing: bool = True,
    rag_retriever=None,
):
    """
    创建完整的审查流水线（含checkpoint持久化）。

    Args:
        with_human_review: 是否启用人机协同
        with_checkpointing: 是否启用checkpoint
        rag_retriever: 可选的 RAG 检索器，用于增强 Agent 上下文

    Returns:
        编译后的可执行图
    """
    graph = build_review_graph(
        with_human_review=with_human_review,
        rag_retriever=rag_retriever,
    )

    compile_kwargs = {}
    if with_checkpointing:
        compile_kwargs["checkpointer"] = MemorySaver()

    return graph.compile(**compile_kwargs)


async def run_review(
    raw_text: str = "",
    document_path: str = "",
    with_human_review: bool = True,
    with_rag: bool = False,
) -> ContractReviewState:
    """
    执行合同审查流水线。

    Args:
        raw_text: 合同原始文本
        document_path: 合同文档路径
        with_human_review: 是否启用人工审核
        with_rag: 是否启用 RAG 检索增强
    """
    rag_retriever = None
    if with_rag:
        try:
            from ..vector.embeddings import EmbeddingModel
            from ..vector.store import VectorStore
            from ..vector.rag import RAGRetriever
            from ..config import Settings

            settings = Settings()
            model = EmbeddingModel(model_name=settings.EMBEDDING_MODEL)
            store = VectorStore(embedding_model=model, index_path=settings.FAISS_INDEX_PATH)
            if store.load():
                rag_retriever = RAGRetriever(store=store)
                logger.info("RAG 检索器已初始化，索引包含 %d 条记录", store.size)
            else:
                logger.info("未找到向量索引，跳过 RAG 增强")
        except Exception as e:
            logger.warning("RAG 初始化失败，使用普通模式: %s", e)

    pipeline = create_review_pipeline(
        with_human_review=with_human_review,
        with_checkpointing=False,
        rag_retriever=rag_retriever,
    )

    initial_state = ContractReviewState(
        raw_text=raw_text,
        document_path=document_path,
        status=ReviewStatus.IN_PROGRESS,
    )

    config = {"configurable": {"thread_id": initial_state.review_id}}
    result = await pipeline.ainvoke(initial_state.model_dump(), config=config)

    return ContractReviewState(**result)
