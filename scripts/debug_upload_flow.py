"""本地驱动完整 HTTP 上传流程（FastAPI TestClient，无需手动起 uvicorn）。

等价于:
    curl -F "file=@contract.pdf" \
         "http://localhost:8000/api/v1/review/upload?mode=sync&with_human_review=false"

开发模式鉴权自动跳过（JWT_SECRET_KEY 用默认值即可，无需 token）。

用法（项目根目录，依赖需已安装）:
    python scripts/debug_upload_flow.py <文件路径> [sync|async]

推荐在 IDE 中对以下位置下断点后运行本脚本，即可单步跟完整个 HTTP 流程:
    src/api/routers/review.py:211   content = await file.read()      # 请求入口，确认收到字节
    src/services/review_service.py:91  file_path.write_bytes(content) # 文件落盘
    src/agents/clause_extraction.py:101  raw_text = self.parser.parse(...)  # 真正解析文件
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import Depends

from src.api.dependencies import MemoryStore
from src.api.exceptions import InvalidFileFormatError
from src.api.routers.metrics import record_review
from src.api.routers.review import _to_summary
from src.api.schemas import ReviewResponse
from src.services import ReviewService
from src.services.deps import get_review_service
from src.services.storage_service import StorageService

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 确保开发模式跳过鉴权 + 不加载种子数据
os.environ.setdefault("JWT_SECRET_KEY", "dev-secret-change-in-production")
os.environ.setdefault("SEED_ON_STARTUP", "false")
# 没有 postgres 时 lifespan 会自动降级为内存存储，这里顺手禁用避免连接等待
os.environ.setdefault(
    "DATABASE_URL", "sqlite+aiosqlite:///:memory:"
)
os.environ.setdefault(
    "DATABASE_URL_SYNC", "sqlite:///:memory:"
)


async def main() -> ReviewResponse:
    # if len(sys.argv) < 2:
    #     print("用法: python scripts/debug_upload_flow.py <文件路径> [sync|async]")
    #     raise SystemExit(1)

    file_path = Path(r"D:\work\code\python\sample-contracts\sample_labor_contract.txt")

    ext = file_path.suffix.lower()
    if ext not in {".pdf", ".docx", ".doc", ".txt"}:
        raise InvalidFileFormatError(format=ext)

    mode= 'sync'
    with_human_review = False
    user_id = 1
    memory = MemoryStore()
    storage = StorageService(db=None, memory=memory)
    svc = ReviewService(storage=storage)


    content = file_path.read_bytes()


    state = await svc.create_from_file(
            filename=file_path.name,
            content=content,
            with_human_review=with_human_review,
        )
    record_review(status=state.status.value, risk_level=state.overall_risk_level.value)
    return _to_summary(state)


if __name__ == "__main__":
    import asyncio

    result = asyncio.run(main())
    print(result.json(indent=2, ensure_ascii=False))
