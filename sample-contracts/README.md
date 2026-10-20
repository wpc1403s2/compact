# 示例合同

本目录包含用于测试与演示的示例合同（故意植入风险与合规问题）。

| 文件 | 类型 | 设计的风险点 |
|------|------|------------|
| `sample_purchase_contract.txt` | 采购合同 | 知识产权全部转让、永久保密、违约金无上限、单方解除权、自动续约、模糊条款 |
| `sample_labor_contract.txt` | 劳动合同 | 试用期过长、甲方随时终止权、薪酬酌情调整、竞业限制违约金偏高 |

可用于：

```bash
# Python / Java / Go 文件审查接口
curl -X POST http://localhost:8000/api/v1/review/file \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@sample_purchase_contract.txt"
```

也可直接粘贴文本内容调用 `POST /api/v1/review`。