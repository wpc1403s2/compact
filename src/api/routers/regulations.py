"""法规管理路由 — 列表、详情、匹配。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/regulations", tags=["regulations"])

# 默认法规数据
DEFAULT_REGULATIONS = [
    {
        "id": "civil-497",
        "name": "《中华人民共和国民法典》",
        "article": "第497条",
        "content": "格式条款有下列情形之一的，该格式条款无效...",
        "category": "civil",
        "keywords": ["免除己方责任", "排除对方主要权利", "加重对方责任", "格式条款"],
        "description": "格式条款无效情形",
    },
    {
        "id": "civil-490",
        "name": "《中华人民共和国民法典》",
        "article": "第490条",
        "content": "当事人采用合同书形式订立合同的，自当事人均签名、盖章或者按指印时合同成立。",
        "category": "civil",
        "keywords": ["口头变更", "书面形式", "合同成立"],
        "description": "合同成立形式",
    },
    {
        "id": "civil-585",
        "name": "《中华人民共和国民法典》",
        "article": "第585条",
        "content": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金...",
        "category": "civil",
        "keywords": ["违约金过高", "违约金过低", "违约金调整"],
        "description": "违约金约定与调整",
    },
    {
        "id": "labor-19",
        "name": "《中华人民共和国劳动合同法》",
        "article": "第19条",
        "content": "劳动合同期限三个月以上不满一年的，试用期不得超过一个月...",
        "category": "labor",
        "keywords": ["试用期超标", "试用期过长", "试用期"],
        "description": "试用期期限规定",
    },
    {
        "id": "labor-22",
        "name": "《中华人民共和国劳动合同法》",
        "article": "第22条",
        "content": "用人单位为劳动者提供专项培训费用，对其进行专业技术培训的，可以与该劳动者订立协议，约定服务期。",
        "category": "labor",
        "keywords": ["培训费用", "服务期", "培训违约金"],
        "description": "培训服务期与违约金",
    },
    {
        "id": "labor-23",
        "name": "《中华人民共和国劳动合同法》",
        "article": "第23条",
        "content": "用人单位与劳动者可以在劳动合同中约定保守用人单位的商业秘密和与知识产权相关的保密事项。",
        "category": "labor",
        "keywords": ["竞业限制", "保密义务", "商业秘密"],
        "description": "竞业限制与保密",
    },
    {
        "id": "labor-25",
        "name": "《中华人民共和国劳动合同法》",
        "article": "第25条",
        "content": "除本法第二十二条和第二十三条规定的情形外，用人单位不得与劳动者约定由劳动者承担违约金。",
        "category": "labor",
        "keywords": ["劳动者违约金", "禁止违约金"],
        "description": "禁止约定违约金的情形",
    },
    {
        "id": "commercial-unfair",
        "name": "《中华人民共和国消费者权益保护法》",
        "article": "第26条",
        "content": "经营者在经营活动中使用格式条款的，应当以显著方式提请消费者注意...",
        "category": "commercial",
        "keywords": ["排除消费者权利", "免除经营者责任", "格式条款"],
        "description": "消费者权益保护",
    },
]


class RegulationResponse(BaseModel):
    id: str
    name: str
    article: str
    content: str
    category: str
    keywords: list[str]
    description: str


class RegulationMatchResponse(BaseModel):
    regulation_id: str
    regulation_name: str
    article: str
    content: str
    matched_keyword: str
    relevance: int


@router.get("", response_model=dict)
async def list_regulations(category: str = None):
    """列出所有法规。"""
    regulations = DEFAULT_REGULATIONS
    if category:
        regulations = [r for r in regulations if r["category"] == category]
    return {"regulations": regulations}


@router.get("/{regulation_id}", response_model=RegulationResponse)
async def get_regulation(regulation_id: str):
    """获取法规详情。"""
    for reg in DEFAULT_REGULATIONS:
        if reg["id"] == regulation_id:
            return reg
    raise HTTPException(status_code=404, detail="法规不存在")


class MatchRequest(BaseModel):
    text: str


@router.post("/match", response_model=dict)
async def match_regulations(request: MatchRequest):
    """匹配文本中的相关法规。"""
    text_lower = request.text.lower()
    matches = []

    for reg in DEFAULT_REGULATIONS:
        for keyword in reg["keywords"]:
            if keyword.lower() in text_lower:
                matches.append({
                    "regulation_id": reg["id"],
                    "regulation_name": reg["name"],
                    "article": reg["article"],
                    "content": reg["content"],
                    "matched_keyword": keyword,
                    "relevance": 80,
                })
                break

    return {"matches": matches}
