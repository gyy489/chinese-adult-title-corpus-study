# Annotation and validation stage

The repository releases the complete frozen v0.3 measurement contract:

- the 42-field [codebook](../../config/annotation_codebook_v0_3.json);
- the [JSON Schema](../../config/annotation_output_schema_v0_3.json);
- the [prompt template](../../prompts/annotation_prompt_v0_3.md); and
- the three-layer runtime validators in this directory.

The validators enforce field sets, enums, sentinel dependencies, stage/argument
consistency, adjudication requirements, and exact-substring evidence checks.
Provider credentials, request logs, model responses, and per-title evidence are
not released. The public validator can be executed without calling a model:

```bash
python -m scripts.validate_annotation_contract
```
