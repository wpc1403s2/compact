"""Run a contract through each review stage locally for IDE debugging.

Usage:
    python scripts/debug_review_stages.py <contract-file>
    python scripts/debug_review_stages.py sample-contracts/sample_labor_contract.txt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.clause_extraction import ClauseExtractionAgent
from src.agents.compliance_check import ComplianceCheckAgent
from src.agents.risk_identification import RiskIdentificationAgent
from src.agents.suggestion import SuggestionAgent
from src.parsers.document_parser import DocumentParser
from src.pipeline.state import ContractReviewState


def _print_stage(title: str, details: dict[str, Any]) -> None:
    print(f"\n{'=' * 20} {title} {'=' * 20}")
    print(json.dumps(details, ensure_ascii=False, indent=2))


async def _run_agent(agent: Any, state: ContractReviewState) -> None:
    updates = await agent.run(state)
    for field, value in updates.items():
        if hasattr(state, field):
            setattr(state, field, value)


async def debug_contract(file_path: Path) -> ContractReviewState:
    file_path = file_path.expanduser().resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"合同文件不存在: {file_path}")

    content = file_path.read_bytes()
    raw_text = DocumentParser().parse(file_path)
    if not raw_text.strip():
        raise ValueError(f"未能从文件中读取到文本: {file_path}")

    state = ContractReviewState(
        document_path=str(file_path),
        original_filename=file_path.name,
        raw_text=raw_text,
    )
    _print_stage(
        "文件读取",
        {
            "path": str(file_path),
            "bytes": len(content),
            "characters": len(raw_text),
            "preview": raw_text[:1000],
        },
    )

    stages = (
        ("条款提取", ClauseExtractionAgent()),
        ("风险识别", RiskIdentificationAgent()),
        ("合规检查", ComplianceCheckAgent()),
        ("修改建议", SuggestionAgent()),
    )
    stage_fields = {
        "条款提取": ("contract_type", "clauses", "entities"),
        "风险识别": ("risk_findings", "overall_risk_level", "risk_summary"),
        "合规检查": ("compliance_findings", "overall_compliance", "missing_clauses"),
        "修改建议": ("suggestions", "version_diff", "needs_human_review"),
    }

    for title, agent in stages:
        await _run_agent(agent, state)
        details = state.model_dump(mode="json", include=set(stage_fields[title]))
        details["errors"] = state.errors
        _print_stage(title, details)

    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="本地逐步调试合同审查 Agent")
    parser.add_argument(
        "file",
        nargs="?",
        type=Path,
        default=ROOT / "sample-contracts" / "sample_labor_contract.txt",
        help="合同文件路径（支持 PDF、DOCX、DOC、TXT）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )
    state = asyncio.run(debug_contract(args.file))
    _print_stage(
        "审查完成",
        {
            "review_id": state.review_id,
            "clauses": len(state.clauses),
            "risks": len(state.risk_findings),
            "compliance_findings": len(state.compliance_findings),
            "suggestions": len(state.suggestions),
            "errors": state.errors,
        },
    )


if __name__ == "__main__":
    main()
