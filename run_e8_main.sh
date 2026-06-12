#!/usr/bin/env bash
cd /data/workspace/emnlp_experiments
echo "===== E8_main: df_agent_v3 + mcp_only × 12 × 8 rep (192 runs) ====="
python -m runner.batch_runner --experiment-id E8_main \
  --tasks all --methods df_agent_v3,mcp_only --reps 8 --timeout-sec 600
echo "===== E8_main DONE ====="
