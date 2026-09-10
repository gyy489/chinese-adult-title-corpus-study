# 中文成人视频标题中的目标—行动者可见性

这是一个面向公开展示的研究结果与复现仓库。项目使用 AI 辅助定量内容分析，研究中文成人视频
标题元数据如何呈现传播目标与传播行动者。仓库按节点展示最终研究管线：采集、清洗、脱敏、
抽样、AI 编码校验、终稿去重、隐私门禁、人工复核、结果分析和发布验收。

[English README](README.md)

## 摘要

成人视频标题作为面向公众的元数据，可能选择性地呈现部分参与者位置，同时不表达另一些位置。
本研究考察中文成人视频标题中传播目标与行动者之间的可见性分配。最终分析样本包含 38,298 个
唯一的最终去标识标题文本，其中 36,303 个来自概率抽样轨道，1,995 个来自预先设定的机制类别
加量轨道。研究使用 42 字段编码工具进行 AI 辅助分析，并在 4,186 个符合条件的标题中考察主要
问题。传播目标在 3,967 个标题中可见（94.77%），行动者在 1,053 个标题中可见（25.16%），
相差 69.61 个百分点。另一项构成分析显示，严格编码为女性的传播目标是最大类别（64.55%）。
这些结果仅描述保留的分析样本；标题不能证明视频内容、现实事件、参与者身份或真实授权状态。

## 仓库内容

- 可配置采集器、页面解析、分页、断点恢复、MySQL 存储与导出代码；
- 按顺序执行的清洗规则引擎、语义长度门禁和最终文本去重；
- 固定随机种子分层抽样与独立标记的机制加量轨道；
- v0.3 的完整 42 字段 Schema、码本、提示词和运行时校验器；
- 精确区间替换、脱敏后重新去重、直接定位符扫描和人工复核聚合程序；
- 18 张聚合结果表、确定性图表、每个关键节点的最终状态清单、测试和 CI。

请先看 **[研究管线图](PIPELINE.md)** 和
[节点状态清单](artifacts/stages/README.md)。仓库不展示多次修改的报告、失败记录或交接稿，
但会展示研究者检查每个关键节点所需的最终代码、公开配置和聚合状态。

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

## 发布边界

仓库不包含真实标题、单标题哈希、记录标识、来源名称与网址、证据片段、模型逐条回答、人工逐条
审核记录、论文全文、投稿文件或第三方论文 PDF。更详细的边界见
[数据可用性](DATA_AVAILABILITY.md)与[隐私及发布范围](PRIVACY_AND_ETHICS.md)。

作者：**谷梓阳（Ziyang Gu）** · [ORCID](https://orcid.org/0009-0002-9998-1568)
