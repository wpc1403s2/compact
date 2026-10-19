"""
法律NLP模块 — 法律文本的自然语言处理能力。

面试亮点：
1. 条款分类（BERT微调 / 关键词+规则 混合方案）
2. 命名实体识别（NER）
3. 语义相似度计算（用于条款比对）
4. 文本摘要

本模块提供两套方案：
- 轻量版：基于关键词和正则表达式（无需GPU，适合部署）
- 完整版：基于预训练模型（需要transformers，效果更好）
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

logger = logging.getLogger(__name__)


# ── 嵌入模型单例 ──────────────────────────────────────────────

_embedding_model = None


def _get_embedding_model():
    """获取全局 EmbeddingModel 单例。"""
    global _embedding_model
    if _embedding_model is None:
        from ..vector.embeddings import EmbeddingModel
        _embedding_model = EmbeddingModel()
    return _embedding_model


# ── 条款分类 ──────────────────────────────────────────────────

CATEGORY_KEYWORDS = {
    "payment": [
        "付款", "支付", "价款", "报酬", "费用", "结算", "货款",
        "预付", "尾款", "账期", "发票",
    ],
    "liability": [
        "责任", "赔偿", "过错", "免责", "连带责任", "损害赔偿",
    ],
    "confidentiality": [
        "保密", "商业秘密", "机密", "不得泄露", "秘密信息",
    ],
    "termination": [
        "终止", "解除", "中止", "到期", "续约", "合同期限",
    ],
    "intellectual_property": [
        "知识产权", "著作权", "专利", "商标", "版权", "源代码",
    ],
    "dispute_resolution": [
        "争议", "仲裁", "诉讼", "管辖", "调解", "协商解决",
    ],
    "force_majeure": [
        "不可抗力", "自然灾害", "战争", "疫情", "政府行为",
    ],
    "warranty": [
        "保证", "担保", "质保", "保修", "承诺",
    ],
    "indemnification": [
        "补偿", "赔偿金", "违约金", "损失赔偿", "经济补偿",
    ],
    "governing_law": [
        "适用法律", "法律适用", "中华人民共和国法律",
    ],
}


def classify_clause(text: str) -> tuple[str, float]:
    """
    条款分类 — 基于关键词匹配的轻量版。

    Returns:
        (category, confidence) 分类结果和置信度
    """
    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[category] = score

    if not scores:
        return "other", 0.0

    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    total_keywords = sum(scores.values())
    confidence = scores[best] / max(total_keywords, 1)
    return best, round(confidence, 2)


# ── 命名实体识别 ──────────────────────────────────────────────

@dataclass
class Entity:
    text: str
    label: str
    start: int
    end: int


ENTITY_PATTERNS = {
    "party_a": [
        r"甲方[：:]\s*(.+?)(?:\s|$|，|。|（)",
        r"甲方(?:为|是)[：:]*\s*(.+?)(?:\s|$|，|。)",
        r"委托(?:方|人)[：:]\s*(.+?)(?:\s|$|，|。)",
    ],
    "party_b": [
        r"乙方[：:]\s*(.+?)(?:\s|$|，|。|（)",
        r"乙方(?:为|是)[：:]*\s*(.+?)(?:\s|$|，|。)",
        r"受托(?:方|人)[：:]\s*(.+?)(?:\s|$|，|。)",
    ],
    "amount": [
        r"(?:人民币|￥|¥)\s*([0-9,，]+\.?[0-9]*)\s*(?:元|万元|万)",
        r"([0-9,，]+\.?[0-9]*)\s*(?:元整|元)",
        r"(?:金额|价款|费用|报酬)(?:为|：|:)\s*(?:人民币)?\s*([0-9,，]+\.?[0-9]*)",
    ],
    "date": [
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
    ],
    "duration": [
        r"(?:期限|有效期)(?:为|：|:)\s*(.+?)(?:。|，|；)",
        r"自.*起[，,]?\s*(?:至|到)\s*(.+?)(?:止|。|，)",
    ],
}


def extract_entities(text: str) -> list[Entity]:
    """
    命名实体识别 — 基于正则表达式的轻量版NER。

    支持识别：甲方、乙方、金额、日期、期限
    """
    entities = []
    for label, patterns in ENTITY_PATTERNS.items():
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                entity_text = match.group(1) if match.groups() else match.group(0)
                if label == "date" and len(match.groups()) >= 3:
                    entity_text = f"{match.group(1)}年{match.group(2)}月{match.group(3)}日"

                entities.append(
                    Entity(
                        text=entity_text.strip(),
                        label=label,
                        start=match.start(),
                        end=match.end(),
                    )
                )
    return entities


# ── 语义相似度 ──────────────────────────────────────────────

def text_similarity(text1: str, text2: str) -> float:
    """
    文本语义相似度计算 — 优先使用 FAISS 嵌入，回退到 SequenceMatcher。

    Returns:
        0.0 ~ 1.0 的相似度分数
    """
    if not text1 or not text2:
        return 0.0
    try:
        model = _get_embedding_model()
        vectors = model.embed([text1, text2])
        vec1 = np.array(vectors[0])
        vec2 = np.array(vectors[1])
        # 向量已 L2 归一化，点积即余弦相似度
        return float(np.dot(vec1, vec2))
    except Exception:
        return SequenceMatcher(None, text1, text2).ratio()


def find_similar_clauses(
    target: str,
    candidates: list[str],
    threshold: float = 0.6,
) -> list[tuple[int, float]]:
    """在候选条款中找到与目标条款相似的条款。"""
    if not target or not candidates:
        return []

    try:
        model = _get_embedding_model()
        all_texts = [target] + candidates
        vectors = model.embed(all_texts)
        target_vec = np.array(vectors[0])
        candidate_vecs = np.array(vectors[1:])

        # 余弦相似度（向量已归一化）
        scores = candidate_vecs @ target_vec

        results = []
        for i, score in enumerate(scores):
            if score >= threshold:
                results.append((i, float(score)))
        results.sort(key=lambda x: -x[1])
        return results
    except Exception:
        results = []
        for i, candidate in enumerate(candidates):
            score = SequenceMatcher(None, target, candidate).ratio()
            if score >= threshold:
                results.append((i, score))
        results.sort(key=lambda x: -x[1])
        return results


# ── 文本预处理 ──────────────────────────────────────────────

def clean_legal_text(text: str) -> str:
    """清洗法律文本：去除多余空白、统一标点。"""
    text = re.sub(r"\s+", " ", text)
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("：", ":").replace("；", ";")
    return text.strip()


def split_sentences(text: str) -> list[str]:
    """中文法律文本分句。"""
    sentences = re.split(r"[。！？；\n]", text)
    return [s.strip() for s in sentences if s.strip()]
