# Conversion and Retention Rate Calculation Rules

This document defines the conversion rate and retention rate metrics recorded per operator node after pipeline execution. These metrics appear in `operators_detail` in the task execution record and are the basis for acceptance verification of multimodal pipeline quality.

## Where the numbers come from

After each operator node completes, the executor records in `operators_detail[op_key]`:

| Field | Type | Description |
|---|---|---|
| `node_id` | string | Stable DAG node id (e.g. `cap`, `asr`, `gen`) |
| `input_count` | int | Number of JSONL lines in this node's input file |
| `sample_count` | int | Number of JSONL lines in this node's output file |
| `conversion_rate` | float | `sample_count / input_count`, rounded to 4 decimal places |
| `output_file` | string | Absolute path to the output JSONL file |

`input_count` is counted from the file that was anchored to the node's `FileStorage` (the dataset file for root nodes, the upstream output file for chain nodes, or the materialised merge file for merge nodes). These fields are written even if the pipeline fails after this node completes, so partial execution results are inspectable.

## Conversion rate (转化率)

**Definition**: `conversion_rate = sample_count / input_count`

**Applies to**: generate-type operators — operators that produce one output row per input row (or more, e.g. augmentation operators). The conversion rate is expected to be 1.0 for most generation operators (every input row produces an output row). Values above 1.0 indicate the operator emitted more rows than it received (fan-out augmentation). Values below 1.0 indicate some rows were dropped or failed.

**Example — `ImageCaptionStub`**:
- Input: 3 records with `image_path` field
- Output: 3 records with `image_path` + `caption` + `caption_model` fields
- `conversion_rate = 3 / 3 = 1.0`

**Example — `ImageGenStub`** (after a merge that produced 3 rows):
- Input: 3 merged records
- Output: 3 records with `generated_image_path` added
- `conversion_rate = 3 / 3 = 1.0`

## Retention rate (留存率)

**Definition**: `retention_rate = output_rows / input_rows` where `output_rows = sample_count`

The retention rate has the same formula as the conversion rate but applies to **filter-type operators** — operators that drop rows from the dataset. For those operators, the expected value is ≤ 1.0.

In `operators_detail`, `conversion_rate` and `retention_rate` share the same field (`conversion_rate`). The semantic distinction is by operator type:

- **Generate operators** (`sample_count ≥ input_count` expected): call it conversion rate.
- **Filter operators** (`sample_count ≤ input_count` expected): call it retention rate.

**Example — `FieldPresentFilterStub`**:
- Input: 5 records; 2 have empty `text` field
- Output: 3 records (2 dropped)
- `conversion_rate = 3 / 5 = 0.6` → read as "retention rate = 60%"

## Merge node counting rule

A merge node has multiple predecessors. Its `input_count` is the number of rows in the **materialised merge file** (after the join), not the sum of its predecessors' output rows. After an id-based join:

- `input_count` = number of rows in the primary branch (matching rows from other branches are merged in as columns, not as new rows)
- `sample_count` = number of rows after the merge operator runs

**Example — fan-out merge**:
- Branch `cap` output: 3 rows (`id=0,1,2`)
- Branch `asr` output: 3 rows (`id=0,1,2`)
- After merge: 3 rows (each row now has both `caption` and `transcript` columns)
- `gen` node receives 3 rows as input → `input_count = 3`
- `gen` node produces 3 rows → `sample_count = 3`, `conversion_rate = 1.0`

If the join is row-order-aligned (no id column), `input_count` is `min(len(branch) for branch in branches)`.

## Reading the metrics from the task result API

```bash
# Get the result for a specific node (by node_id)
curl '/api/v1/tasks/execution/{task_id}/result?node=gen&limit=5'
```

Response includes:
```json
{
  "node_id": "gen",
  "operators_detail": {
    "ImageGenStub_2": {
      "node_id": "gen",
      "input_count": 3,
      "sample_count": 3,
      "conversion_rate": 1.0,
      "output_file": "/path/to/cache/.../nodes/gen/dataflow_cache_step_step1.jsonl"
    }
  }
}
```

## Demo pipeline worked example

Using the `mm_demo_pipeline.json` fixture (see [operator-integration-guide.md](operator-integration-guide.md)):

Dataset: 3 records (`id: 0, 1, 2`), each with `image_path` and `audio_path`.

| Node | Operator | input_count | sample_count | conversion_rate | Notes |
|---|---|---|---|---|---|
| `cap` | ImageCaptionStub | 3 | 3 | 1.0 | reads dataset; adds caption, caption_model |
| `asr` | AudioTranscribeStub | 3 | 3 | 1.0 | reads dataset in parallel; adds transcript |
| `gen` | ImageGenStub | 3 | 3 | 1.0 | reads merged output; adds generated_image_path |

To reproduce: run `backend/tests/test_mm_stubs_e2e.py` (requires open-dataflow installed). The test asserts all three node output files exist and that the conversion rates are present in `operators_detail`.

## Acceptance verification procedure

For external acceptance, the verifier should:

1. Register a multimodal dataset (or use the e2e test fixture dataset).
2. Execute the demo pipeline (`mm_demo_pipeline.json` with a real dataset id).
3. Fetch `GET /api/v1/tasks/execution/{task_id}/result` and check that:
   - All three nodes have `status: "completed"`.
   - `operators_detail[*].output_file` exists on disk.
   - `operators_detail[*].conversion_rate` is present.
   - The merge node's output file contains records with both `caption` and `transcript` fields.
   - The `gen` node's output file contains records with `generated_image_path` pointing to an existing PNG file.
4. Optionally fetch sample media via `GET /api/v1/tasks/execution/{task_id}/media?node=gen&row=0&field=generated_image_path` and verify the response is a valid PNG.
