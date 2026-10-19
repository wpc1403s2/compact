"""审查服务 — 合同审查的核心业务逻辑编排。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path

from ..pipeline.graph import create_review_pipeline, set_progress_callback
from ..pipeline.state import (
    ContractReviewState,
    HumanFeedback,
    ReviewStatus,
)
from .storage_service import StorageService

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}

# 批量审查配置
BATCH_MAX_FILES = 10
BATCH_MAX_CONCURRENT = 3

# 防止 asyncio.create_task 的任务被 GC
_BACKGROUND_TASKS: set[asyncio.Task] = set()


class ReviewService:
    """审查服务：编排流水线执行、存储、反馈处理。

    支持同步（await 完整结果）和异步（立即返回 review_id，后台运行并通过回调持久化进度）两种模式。
    """

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage
        self._tenant_id = storage._tenant_id
        self._user_id = ""  # 由调用方在需要时设置
        # 暴露给后台任务使用的 memory 引用
        self._memory = storage._memory

    # ── 创建审查（异步提交，立即返回 review_id）───────────────

    async def start_from_text(
        self,
        text: str,
        with_human_review: bool = True,
        user_id: str = "",
    ) -> str:
        """异步启动：保存 PENDING 状态，后台执行流水线，立即返回 review_id。"""
        review_id = str(uuid.uuid4())
        initial_state = ContractReviewState(
            review_id=review_id,
            raw_text=text,
            status=ReviewStatus.PENDING,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await self._persist_state(initial_state, user_id=user_id or self._user_id)

        task = asyncio.create_task(
            self._run_pipeline_with_progress(
                review_id=review_id,
                initial_state=initial_state,
                with_human_review=with_human_review,
                user_id=user_id or self._user_id,
            )
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return review_id

    async def start_from_file(
        self,
        filename: str,
        content: bytes,
        with_human_review: bool = True,
        user_id: str = "",
    ) -> str:
        """异步启动：保存文件 + PENDING 状态，后台执行流水线。"""
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: {ext}")

        file_id = str(uuid.uuid4())
        file_path = UPLOAD_DIR / f"{file_id}{ext}"
        file_path.write_bytes(content)

        review_id = str(uuid.uuid4())
        initial_state = ContractReviewState(
            review_id=review_id,
            document_path=str(file_path),
            original_filename=filename,
            status=ReviewStatus.PENDING,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await self._persist_state(initial_state, user_id=user_id or self._user_id)

        task = asyncio.create_task(
            self._run_pipeline_with_progress(
                review_id=review_id,
                initial_state=initial_state,
                with_human_review=with_human_review,
                user_id=user_id or self._user_id,
            )
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return review_id

    # ── 创建审查（同步等待完整结果）─────────────────────────────

    async def create_from_text(
        self,
        text: str,
        with_human_review: bool = True,
    ) -> ContractReviewState:
        """从文本创建审查（同步，返回完整 state）。"""
        review_id = await self.start_from_text(text, with_human_review=with_human_review)
        return await self._wait_for_review(review_id)

    async def create_from_file(
        self,
        filename: str,
        content: bytes,
        with_human_review: bool = True,
    ) -> ContractReviewState:
        """从上传文件创建审查（同步，返回完整 state）。"""
        review_id = await self.start_from_file(filename, content, with_human_review=with_human_review)
        return await self._wait_for_review(review_id)

    async def _wait_for_review(self, review_id: str, poll_interval: float = 0.3, timeout: float = 300.0) -> ContractReviewState:
        """等待后台审查任务完成，返回最终 state。"""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            state = await self._load_state_detached(review_id)
            if state is None:
                raise RuntimeError(f"审查记录丢失: {review_id}")
            if state.status in (
                ReviewStatus.COMPLETED,
                ReviewStatus.APPROVED,
                ReviewStatus.REJECTED,
                ReviewStatus.AWAITING_HUMAN,
            ):
                return state
            last_error_step = next((e for e in reversed(state.step_log) if e.status == "error"), None)
            if last_error_step is not None and state.current_step == "":
                await asyncio.sleep(poll_interval)
                return await self._load_state_detached(review_id) or state
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"审查超时: {review_id}")
            await asyncio.sleep(poll_interval)

    # ── 查询审查 ──────────────────────────────────────────────

    async def get(self, review_id: str) -> ContractReviewState | None:
        """获取审查详情。"""
        return await self._storage.load(review_id)

    async def list(self, offset: int = 0, limit: int = 20) -> list[dict]:
        """列出审查记录（分页）。"""
        return await self._storage.list_reviews(offset=offset, limit=limit)

    async def count(self) -> int:
        """统计审查记录总数。"""
        return await self._storage.count_reviews()

    # ── 人工反馈 ──────────────────────────────────────────────

    async def submit_feedback(
        self,
        review_id: str,
        reviewer: str,
        decision: str,
        comments: str = "",
    ) -> ReviewStatus:
        """提交人工审核反馈，返回新状态。"""
        state = await self._storage.load(review_id)
        if state is None:
            raise ValueError(f"审查记录不存在: {review_id}")

        feedback = HumanFeedback(
            reviewer=reviewer,
            decision=decision,
            comments=comments,
            timestamp=datetime.now(),
        )

        new_status = self._resolve_status(decision)
        await self._storage.update_feedback(review_id, feedback, new_status)
        return new_status

    # ── 删除与重跑 ──────────────────────────────────────────────

    async def delete(self, review_id: str) -> bool:
        """删除审查记录及其所有子数据。"""
        return await self._storage.delete(review_id)

    async def rerun(
        self,
        review_id: str,
        with_human_review: bool = True,
        async_mode: bool = False,
        user_id: str = "",
    ) -> ContractReviewState | str:
        """基于已存原文/原文件重新执行审查，生成新 review_id 的记录。

        async_mode=True 时返回新 review_id（后台执行），否则等待完成并返回 state。
        """
        old = await self._storage.load(review_id)
        if old is None:
            raise ValueError(f"审查记录不存在: {review_id}")

        new_id = str(uuid.uuid4())
        initial_state = ContractReviewState(
            review_id=new_id,
            raw_text=old.raw_text,
            document_path=old.document_path,
            original_filename=old.original_filename,
            status=ReviewStatus.PENDING,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await self._persist_state(initial_state, user_id=user_id or self._user_id)

        task = asyncio.create_task(
            self._run_pipeline_with_progress(
                review_id=new_id,
                initial_state=initial_state,
                with_human_review=with_human_review,
                user_id=user_id or self._user_id,
            )
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

        if async_mode:
            return new_id
        # 同步模式：等待完成
        return await self._wait_for_review(new_id)

    # ── 批量审查 ──────────────────────────────────────────────

    async def create_batch(
        self,
        files: list[tuple[str, bytes]],
        with_human_review: bool = True,
    ) -> tuple[str, list[dict]]:
        """批量创建审查任务。

        Args:
            files: [(filename, content), ...] 文件列表
            with_human_review: 是否启用人工审核

        Returns:
            (batch_id, items) 批次ID和每个文件的状态
        """
        if len(files) > BATCH_MAX_FILES:
            raise ValueError(f"单次最多上传 {BATCH_MAX_FILES} 个文件")

        batch_id = str(uuid.uuid4())[:12]
        items = []

        review_ids = []
        for filename, content in files:
            ext = Path(filename).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                items.append({
                    "review_id": "",
                    "filename": filename,
                    "status": "failed",
                    "error": f"不支持的文件格式: {ext}",
                })
                continue

            file_id = str(uuid.uuid4())
            file_path = UPLOAD_DIR / f"{file_id}{ext}"
            file_path.write_bytes(content)

            rid = str(uuid.uuid4())
            review_ids.append((rid, str(file_path), filename))
            items.append({
                "review_id": rid,
                "filename": filename,
                "status": "pending",
                "error": "",
            })

        semaphore = asyncio.Semaphore(BATCH_MAX_CONCURRENT)

        async def _process_one(rid: str, fpath: str, fname: str, idx: int):
            async with semaphore:
                try:
                    initial_state = ContractReviewState(
                        review_id=rid,
                        document_path=fpath,
                        original_filename=fname,
                        status=ReviewStatus.PENDING,
                    )
                    state = await self._run_pipeline_standalone(
                        rid, initial_state, with_human_review
                    )
                    await self._storage.save(state)
                    items[idx]["status"] = state.status.value
                    items[idx]["review_id"] = rid
                except Exception as e:
                    logger.error("批量审查失败 file=%s: %s", fname, e)
                    items[idx]["status"] = "failed"
                    items[idx]["error"] = str(e)

        tasks = [
            _process_one(rid, fpath, fname, i)
            for i, (rid, fpath, fname) in enumerate(review_ids)
        ]
        await asyncio.gather(*tasks)

        return batch_id, items

    async def get_batch_status(self, batch_id: str) -> dict | None:
        return None

    # ── 内部：后台执行流水线并通过回调持久化进度 ────────────────

    async def _run_pipeline_with_progress(
        self,
        review_id: str,
        initial_state: ContractReviewState,
        with_human_review: bool,
        user_id: str = "",
    ) -> ContractReviewState:
        """在后台执行流水线，每步开始/结束时持久化 state。"""

        async def _on_progress(state: ContractReviewState) -> None:
            state.updated_at = datetime.now()
            await self._persist_state(state, user_id=user_id)

        # 注册按 review_id 键控的进度回调（支持并发）
        set_progress_callback(review_id, _on_progress)
        try:
            initial_state.status = ReviewStatus.IN_PROGRESS
            initial_state.updated_at = datetime.now()
            await self._persist_state(initial_state, user_id=user_id)

            pipeline = create_review_pipeline(
                with_human_review=with_human_review,
                with_checkpointing=False,
            )
            config = {"configurable": {"thread_id": review_id}}
            result = await pipeline.ainvoke(initial_state.model_dump(), config=config)
            if isinstance(result, ContractReviewState):
                final_state = result
            elif isinstance(result, dict):
                final_state = ContractReviewState(**result)
            else:
                final_state = ContractReviewState.model_validate(result)
            final_state.updated_at = datetime.now()
            await self._persist_state(final_state, user_id=user_id)
            logger.info(
                "审查完成 review_id=%s status=%s progress=%.0f%%",
                review_id, final_state.status.value, final_state.progress * 100,
            )
            return final_state
        except Exception as e:
            logger.exception("流水线执行失败 review_id=%s", review_id)
            # 以 best-effort 方式把错误写回存储
            try:
                current = await self._storage.load(review_id)
                if current is not None:
                    err_msg = f"pipeline: {e}"
                    if err_msg not in current.errors:
                        current.errors.append(err_msg)
                    current.updated_at = datetime.now()
                    await self._persist_state(current, user_id=user_id)
            except Exception:
                logger.exception("持久化错误状态失败")
            raise
        finally:
            set_progress_callback(review_id, None)

    async def _run_pipeline_standalone(
        self,
        review_id: str,
        initial_state: ContractReviewState,
        with_human_review: bool,
    ) -> ContractReviewState:
        """批量等无需进度回调的路径：直接执行。"""
        pipeline = create_review_pipeline(
            with_human_review=with_human_review,
            with_checkpointing=False,
        )
        config = {"configurable": {"thread_id": review_id}}
        initial_state.status = ReviewStatus.IN_PROGRESS
        result = await pipeline.ainvoke(initial_state.model_dump(), config=config)
        if isinstance(result, ContractReviewState):
            return result
        if isinstance(result, dict):
            return ContractReviewState(**result)
        return ContractReviewState.model_validate(result)

    async def _load_state_detached(self, review_id: str) -> ContractReviewState | None:
        """绕开请求级 session，每次用新 session/memory 读取最新状态（用于轮询）。"""
        if not self._storage.is_persistent:
            data = self._memory.get(review_id)
            return ContractReviewState(**data) if data else None
        from ..db.session import async_session_factory
        from ..db.crud import get_review as crud_get
        if async_session_factory is None:
            data = self._memory.get(review_id)
            return ContractReviewState(**data) if data else None
        async with async_session_factory() as session:
            return await crud_get(session, review_id, tenant_id=self._tenant_id)

    async def _persist_state(self, state: ContractReviewState, user_id: str = "") -> None:
        """把 state 持久化到存储。

        后台任务运行时请求级 DB session 已关闭，因此 DB 模式下每次都新建一个短 session；
        内存模式直接写入共享 MemoryStore。
        """
        try:
            if not self._storage.is_persistent:
                self._memory.set(state.review_id, state.model_dump(mode="json"))
                return

            from ..db.session import async_session_factory
            from ..db.crud import save_review as crud_save

            if async_session_factory is None:
                self._memory.set(state.review_id, state.model_dump(mode="json"))
                return

            async with async_session_factory() as session:
                async with session.begin():
                    await crud_save(
                        session,
                        state,
                        tenant_id=self._tenant_id,
                        user_id=user_id or self._user_id,
                    )
        except Exception:
            logger.exception("后台持久化 state 失败 review_id=%s", state.review_id)

    @staticmethod
    def _resolve_status(decision: str) -> ReviewStatus:
        """将决策字符串映射为 ReviewStatus。"""
        mapping = {
            "approve": ReviewStatus.APPROVED,
            "reject": ReviewStatus.REJECTED,
        }
        return mapping.get(decision, ReviewStatus.COMPLETED)
