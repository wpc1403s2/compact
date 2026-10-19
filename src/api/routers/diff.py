"""版本对比路由 — 文本差异比较。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/diff", tags=["diff"])


class DiffRequest(BaseModel):
    old_text: str
    new_text: str


@router.post("")
async def compare_diff(request: DiffRequest):
    """比较两个文本的差异。"""
    old_lines = request.old_text.split("\n")
    new_lines = request.new_text.split("\n")

    items = []
    added = removed = changed = unchanged = 0

    max_len = max(len(old_lines), len(new_lines))

    for i in range(max_len):
        old_line = old_lines[i] if i < len(old_lines) else ""
        new_line = new_lines[i] if i < len(new_lines) else ""

        if old_line == new_line:
            items.append({"type": "unchanged", "line": i + 1, "content": old_line})
            unchanged += 1
        elif old_line == "" and new_line != "":
            items.append({"type": "added", "line": i + 1, "content": new_line, "new_value": new_line})
            added += 1
        elif old_line != "" and new_line == "":
            items.append({"type": "removed", "line": i + 1, "content": old_line, "old_value": old_line})
            removed += 1
        else:
            items.append({"type": "changed", "line": i + 1, "content": f"{old_line} → {new_line}", "old_value": old_line, "new_value": new_line})
            changed += 1

    total = added + removed + changed
    summary = f"共 {total} 行差异：新增 {added} 行，删除 {removed} 行，修改 {changed} 行，未变 {unchanged} 行"

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
        "items": items,
        "summary": summary,
    }
