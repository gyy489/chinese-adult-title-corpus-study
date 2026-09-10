# 权威程序索引

本页只列最终研究链及其必要的版本化执行程序，不保存阶段报告、交接稿、人工逐条记录或失败
叙事。数量口径以 `PIPELINE.md` 和 `artifacts/stages/` 为准。

## 公开代码的三种状态

| 状态 | 含义 |
| --- | --- |
| `production` | 实际生产程序中不涉及敏感字面的部分，可直接审查 |
| `pseudonymized` | 算法与控制流保留，真实来源名统一替换为 `source_01`—`source_10` |
| `public-interface` | 生产程序依赖的真实姓名、机构、品牌或来源字典不公开；保留调用契约、结构规则和可注入接口 |

这些状态用于诚实区分“代码公开”和“真实数据可复现”。公开仓库不声称能够在缺少受限标题语料
与受限字典时重新得到标题级结果。

## 1. 两轮采集

状态：采集引擎为 `production`；生产来源配置与导出清单为 `pseudonymized`。

- `src/collection/crawler.py`：HTTP／Playwright 抓取、列表与详情解析、域名约束；
- `src/collection/mysql_store.py`：幂等写入和状态保存；
- `src/collection/crawl_status.py`：断点与覆盖情况检查；
- `src/collection/export_mysql_dataset.py`：数据库导出；
- `src/collection/build_export_manifest.py`：两轮输出计数和校验和，来源仅用匿名编号；
- `config/collection_rounds_public.json`：2026-05-08—06-17 与 2026-08-02—03 两轮边界。

## 2. 合并、清洗与准备层

状态：结构和通用规则为 `production`；来源特定分支为 `pseudonymized`；闭合字典为
`public-interface`。

- `src/preprocessing/build_combined_deduplicated_manifest.py`：跨轮合并与来源记录去重清单；
- `src/preprocessing/extract_bracket_content.py`、`clean_titles_bracket_content.py`、
  `draft_bracket_coding.py`：括号抽取、分类与清理；
- `src/preprocessing/apply_full_cleanup_pipeline_combined.py`：按冻结顺序串联清洗规则；
- `src/preprocessing/apply_source_08_ai_residue_cleanup.py`：匿名化来源特定残留清理；
- `src/preprocessing/brand_and_code_rule.py`：通用结构规则及受限字典注入接口；
- `src/preprocessing/apply_text_cleanup_rules.py`、`normalize_cjk_radical_variants.py`、
  `apply_freetext_marketing_cleanup.py`、`apply_date_stamp_cleanup.py`、
  `apply_truncation_marker_cleanup.py`：各个独立清洗节点；
- `src/preprocessing/apply_length_forum_exclusion_combined.py`、
  `apply_non_cjk_exclusion_combined.py`、`filter_serial_story_posts.py`：显式范围排除；
- `src/preprocessing/export_combined_review_and_analysis_views.py`：保留审计字段并生成后续视图。

`config/preprocessing_stage_contract.json` 给出最终顺序；`src/preprocessing/pipeline.py` 是对该
契约的紧凑、合成数据可运行实现。

## 3. 去标识、长度门禁与候选框

- `src/preprocessing/detect_deidentification_candidates.py`：`public-interface`，保留结构／NER
  层，真实人名和机构词表不公开；
- `src/preprocessing/apply_deidentification.py`：只按已审核的精确 span 替换，保留源文本层；
- `src/preprocessing/scan_deidentification_residuals.py`：`public-interface`，保留直接定位符
  结构扫描；
- `src/analysis/build_gendered_argument_visibility_full_v03_90727_v1.py`：生成 90,727 条候选
  记录的冻结执行框；
- `src/preprocessing/pipeline.py`：公开的语义 CJK 长度与确定性精确文本去重函数。

## 4. 固定设计与两阶段 AI 编码

- `src/analysis/build_gendered_argument_visibility_scaleup_5000_v1.py`：构建第一阶段固定
  5,000 条（3,000 概率轨道＋2,000 机制加量轨道）；
- `src/analysis/run_gendered_argument_visibility_scaleup_5000_v1.py`：第一阶段批量执行与恢复；
- `src/analysis/audit_gendered_argument_visibility_scaleup_5000_v1.py`：合同、覆盖与成对指标审计；
- `src/analysis/build_gendered_argument_visibility_budget_adaptive_20000_v1.py`：第二阶段固定种子
  分层嵌套顺序；
- `src/analysis/run_gendered_argument_visibility_budget_adaptive_20000_v1.py`：第二阶段批量执行、
  失败项重试与状态保存；
- `src/analysis/run_gendered_asymmetric_visibility_full_annotation_v1.py` 及其三个合同／构建模块：
  两个生产 runner 复用的事务状态、锁、封存输出和失败关闭后端；
- `src/analysis/audit_gendered_argument_visibility_budget_adaptive_20000_v1.py`：第二阶段完整性审计；
- `src/analysis/integrate_gendered_argument_visibility_full_v03_90727_v1.py`：合并冻结输出；
- `src/analysis/gendered_argument_visibility_contract_v0_1.py`—`v0_3.py`：版本化运行时合同；
- `config/gendered_argument_visibility_codebook_v0_1.json`—`v0_3.json`、对应 Schema 与
  `prompts/`：版本化测量工具。

仓库中的 provider 授权文件是默认拒绝的公开模板；任何 API 调用都不会因克隆仓库而自动获准。

## 5. 最终文本去重与 v4 隐私修复

- `src/analysis/build_gendered_visibility_text_deduplicated_corpus_v2.py`：对 38,623 条有效
  记录级输出执行精确最终文本去重；
- `src/analysis/audit_gendered_visibility_duplicate_representatives_v1.py`：重复组代表项审计；
- `src/analysis/audit_residual_privacy_paper_corpus_v1.py`、`v3.py`：残余隐私扫描和聚合审计；
- `src/analysis/build_gendered_visibility_privacy_remediation_v2.py`：111 条确认标题的精确 span
  修复、重新哈希和再去重；
- `src/analysis/audit_gendered_visibility_privacy_label_dependency_v2.py`、
  `audit_gendered_visibility_privacy_label_invariance_v1.py`：标签依赖与不变性检查；
- `src/analysis/activate_gendered_visibility_paper_corpus_v4.py`：激活 38,298 个唯一最终文本；
- `src/review_app/privacy_span_*`：本地精确 span 审核界面与存储。

真实候选字符串、修复前证据和逐条决定不进入本仓库。

## 6. 双人复核与 AI 准确性

- `src/analysis/build_gendered_visibility_human_review_400_v1.py`：固定 400 标题样本；
- `src/review_app/server.py`、`store.py`、`static/`：本地盲审界面；
- `src/review_app/reference_viewer.py`、`comparison_viewer.py`：封存参考与完成后比较；
- `src/analysis/summarize_gendered_visibility_human_audit_400_v2.py`、
  `summarize_gendered_visibility_human_audit_rq1_v2.py`：一致性、混淆矩阵和 AI 对照汇总。

该复核只评价 AI 编码准确性，不是金标准，也不替代全语料 AI 标签。

## 7. 最终分析、表格与图形

- `src/analysis/analyze_gendered_visibility_paper_corpus_v1.py`：38,298 文本的主分析与敏感性分析；
- `src/analysis/analyze_gendered_argument_visibility_deep_mining_v1.py`：字段级关系和补充指标；
- `src/analysis/build_gendered_visibility_manuscript_tables_v1.py`：生成公开聚合表；
- `src/analysis/build_gendered_visibility_manuscript_figures_v1.py`：生成确定性图形；
- `scripts/reproduce_results.py`：在不接触标题级数据时，从已公开聚合表复核数字关系并重建摘要图。

前四个程序公开完整分析逻辑，但因标题—标签矩阵受限，真实语料重跑只能在私有研究环境中完成。

## 8. 发布门禁

- `scripts/verify_release.py`：拒绝敏感路径、逐条字段、凭据、本地绝对路径与未登记文件；
- `scripts/build_manifest.py`：为全部发布文件生成 SHA-256 清单；
- `.github/workflows/ci.yml`：在 Python 3.11 与 3.12 上执行边界检查、结果复现与测试。
