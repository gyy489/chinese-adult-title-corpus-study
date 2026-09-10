# 中文成人视频标题中的目标—行动者可见性

[![发布安全与复现验证](https://github.com/gyy489/chinese-adult-title-corpus-study/actions/workflows/ci.yml/badge.svg)](https://github.com/gyy489/chinese-adult-title-corpus-study/actions/workflows/ci.yml)

这是一个面向公开展示的研究结果与复现仓库。项目使用 AI 辅助定量内容分析，研究中文成人视频
标题元数据如何呈现传播目标与传播行动者。仓库按节点展示最终研究管线：采集、清洗、脱敏、
抽样、AI 编码校验、终稿去重、隐私门禁、人工复核、结果分析和发布验收。

[英文说明](README.en.md)

> 前几年大家都在谈论女权主义,网络上出现了很多不同观点,这让我在很多事情上想不通
>
> 然后,一次偶然的机会，为了艺术创作，我进行了一个简单的调查
>
> 我发现，在中文色情电影中，有一些不正常的词汇组合，例如：妈妈和强奸，爸爸和偷拍
>
> 我认为这是突破道德下限的，而于此同时，经过调查，我发现国内对于中文色情电影的学术研究非常少
>
> 我认为性别关系往往会体现在性爱关系上
>
> 因此，我决心对这个主题进行一些研究，便有了这个项目
>
> **以下是我的论文摘要：**

## 摘要

成人视频标题作为面向公众的元数据，可能选择性地呈现部分参与者位置，同时不表达另一些位置。
本研究考察中文成人视频标题中传播目标与行动者之间的可见性分配。最终分析样本包含 38,298 个
唯一的最终去标识标题文本，其中 36,303 个来自概率抽样轨道，1,995 个来自预先设定的机制类别
加量轨道。研究使用 42 字段编码工具进行 AI 辅助分析，并在 4,186 个符合条件的标题中考察主要
问题。传播目标在 3,967 个标题中可见（94.77%），行动者在 1,053 个标题中可见（25.16%），
相差 69.61 个百分点。另一项构成分析显示，严格编码为女性的传播目标是最大类别（64.55%）。
这些结果仅描述保留的分析样本；标题不能证明视频内容、现实事件、参与者身份或真实授权状态。

## 数据角色

```text
两轮十来源采集（103,743 条来源记录）
→ 清洗与去标识（91,172 条记录／89,538 个唯一最终文本）
→ 语义长度门禁（90,727 条候选记录／89,161 个唯一候选文本）
→ 固定种子分层概率轨道＋预先设定的机制加量轨道
→ 42 字段 v0.3 AI 编码（38,623 条合约有效的记录级输出）
→ 第一次最终文本去重（38,300 个唯一文本）
→ 研究者隐私复核、精确区间替换与再去重
→ 最终分析语料（38,298 个唯一去标识文本）
   ├─ 36,303 个概率轨道文本
   └─ 1,995 个机制加量轨道文本
→ 聚合分析与发布验收（主要比较的机会分母为 4,186）
```

分析单位是一个唯一的最终去标识标题文本，不是来源记录、影片、场景、事件或人物。
公开仓库只提供聚合结果，不包含用于恢复上述成员关系的标题级索引。

## 仓库内容

- 两轮实际共用的可配置采集器、页面解析、分页、断点恢复、MySQL 存储与导出代码；
- 各轮清洗脚本、合并清洗编排、语义长度门禁和最终文本去重程序；
- 固定随机种子分层抽样与独立标记的机制加量轨道；
- v0.3 的完整 42 字段 Schema、码本、提示词和运行时校验器；
- 两阶段 AI 编码的样本构建、批量运行、失败项恢复、合同审计和输出整合程序；
- 精确区间替换、脱敏后重新去重、直接定位符扫描、本地审核界面和人工复核聚合程序；
- 真实标题级分析、表格与图形的生成程序，以及不接触敏感数据的公开聚合复核程序；
- 18 张聚合结果表、确定性图表、每个关键节点的最终状态清单、测试和 CI。

请先看 **[研究管线图](PIPELINE.md)**、[权威程序索引](PROGRAM_INVENTORY.md) 和
[节点状态清单](artifacts/stages/README.md)。仓库不展示多次修改的报告、失败记录或交接稿，
但会展示研究者检查每个关键节点所需的最终代码、公开配置和聚合状态。

## 当前文件入口

- 完整管线、数量流和公开边界：[`PIPELINE.md`](PIPELINE.md)
- 每个节点实际使用的程序与公开状态：[`PROGRAM_INVENTORY.md`](PROGRAM_INVENTORY.md)
- 每个关键节点的最终聚合状态：[`artifacts/stages/`](artifacts/stages/)
- 采集、断点恢复、存储与导出：[`src/collection/`](src/collection/)
- 清洗、语义长度门禁和精确文本去重：[`src/preprocessing/`](src/preprocessing/)
- 公开的最终预处理顺序：[`config/preprocessing_stage_contract.json`](config/preprocessing_stage_contract.json)
- 固定种子分层抽样与机制加量：[`src/sampling/`](src/sampling/)
- 42 字段码本、Schema、提示词和校验器：[`src/annotation/`](src/annotation/)
- 精确区间隐私修复与直接定位符门禁：[`src/privacy/`](src/privacy/)
- 人工复核一致性与 AI 准确性聚合：[`src/human_review/`](src/human_review/)
- 本地盲审、完成后比较与隐私 span 审核界面：[`src/review_app/`](src/review_app/)
- 两阶段 AI 编码、最终语料激活、分析和制表制图：[`src/analysis/`](src/analysis/)
- 18 张公开聚合结果表：[`results/tables/`](results/tables/)
- 确定性结果复现：[`scripts/reproduce_results.py`](scripts/reproduce_results.py)
- 发布安全检查与 SHA-256 清单：[`scripts/verify_release.py`](scripts/verify_release.py)、[`results/release_manifest.json`](results/release_manifest.json)

## 快速验证

```bash
python scripts/verify_release.py
python scripts/reproduce_results.py --check
python scripts/build_manifest.py --check
python -m unittest discover -s tests -v
```

公开程序可以复核已发布聚合表之间的数字关系，并重新生成公开汇总和图形。由于标题级研究语料
不公开，本仓库不宣称能够从原始标题重新完成全量分析。

要用无敏感内容的合成输入跑通各阶段接口：

```bash
python -m pip install -r requirements-public.txt
python -m scripts.demo_collection
python -m scripts.demo_preprocessing
python -m scripts.demo_sampling
python -m scripts.validate_annotation_contract
python -m scripts.demo_privacy_gate
python -m scripts.demo_human_review
```

`requirements-public.txt` 是 CI 与合成示例所需的最小环境；
`requirements-research-code.txt` 列出所公开生产程序的完整 Python 包层。后者不会提供真实语料、
受限词表、浏览器二进制、NER 模型、审核队列或 API 凭据。

## 发布边界

仓库不包含真实标题、单标题哈希、记录标识、来源名称与网址、证据片段、模型逐条回答、人工逐条
审核记录、论文全文、投稿文件或第三方论文 PDF。更详细的边界见
[数据可用性](DATA_AVAILABILITY.md)与[隐私及发布范围](PRIVACY_AND_ETHICS.md)。

作者：**谷梓阳（Ziyang Gu）** · [ORCID](https://orcid.org/0009-0002-9998-1568)
