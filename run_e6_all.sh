#!/usr/bin/env bash
cd /data/workspace/emnlp_experiments
echo "===== [1/3] E6_dcc_full: direct_cc × 12 × 3 ====="
python -m runner.batch_runner --experiment-id E6_dcc_full \
  --tasks all --methods direct_cc --reps 3 --timeout-sec 900

echo "===== [2/3] E6_ablation: no_mcp,no_dag_sync,no_exec_gating × 12 × 3 ====="
python -m runner.batch_runner --experiment-id E6_ablation \
  --tasks all --methods no_mcp,no_dag_sync,no_exec_gating --reps 3 --timeout-sec 600

echo "===== [3/3] E6_rep_extend: df_agent_v3,mcp_only × 12 × reps3-7 ====="
python -m runner.batch_runner --experiment-id E6_rep_extend \
  --tasks all --methods df_agent_v3,mcp_only --reps 5 --start-rep 3 --timeout-sec 600

echo "===== ALL E6 BATCHES DONE ====="
