#!/usr/bin/env bash
# Master experiment orchestrator: runs the rest of the campaign after
# skill_ablation completes. Restarts backend, waits for readiness, then
# runs E1-fill, E3-v2, and E2 ablation in sequence.
#
# Usage:
#   ./orchestrate.sh       # foreground
#   nohup ./orchestrate.sh > logs/orchestrate.log 2>&1 &
set -u
cd /data/workspace/emnlp_experiments

STAGE_LOG=/data/workspace/emnlp_experiments/logs/orchestrate.log
mkdir -p logs

log() { echo "[orchestrate $(date +%H:%M:%S)] $*" | tee -a "$STAGE_LOG"; }

# --- 1. wait for skill_ablation to finish ---
log "waiting for skill_ablation batch_runner (pid 3015212) to finish ..."
while kill -0 3015212 2>/dev/null; do
  sleep 60
done
log "skill_ablation finished."

# --- 2. aggregate skill_ablation ---
log "aggregating skill_ablation ..."
python3 analysis/aggregate.py --experiment-id skill_ablation >> "$STAGE_LOG" 2>&1
python3 analysis/failures.py skill_ablation >> "$STAGE_LOG" 2>&1

# --- 3. restart backend so new deps take effect ---
log "restarting backend ..."
pkill -9 -f "uvicorn app.main:app" 2>/dev/null
sleep 3
cd /data/workspace/df_web_and_skills/DataFlow-WebUI/backend
setsid uvicorn app.main:app --port 8000 --host 0.0.0.0 \
  >/data/workspace/emnlp_experiments/logs/backend.log 2>&1 < /dev/null &
sleep 5
cd /data/workspace/emnlp_experiments

# Wait for backend readiness
for i in $(seq 1 30); do
  if curl -s --max-time 3 http://localhost:8000/api/v1/datasets/ | grep -q '"success":true'; then
    log "backend ready after ${i}0s"
    break
  fi
  sleep 10
done

# --- 4. re-register gpt-4o serving (datasets persist, serving does too in most setups) ---
SRV_COUNT=$(curl -s http://localhost:8000/api/v1/serving/ | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('data') or []))")
log "serving count after restart: $SRV_COUNT"
if [ "$SRV_COUNT" = "0" ]; then
  log "re-registering gpt-4o serving ..."
  curl -sS -X POST "http://localhost:8000/api/v1/serving/?name=emnlp_gpt4o&cls_name=APILLMServing_request" \
    -H "Content-Type: application/json" \
    -d '[
      {"name": "api_url", "value": "${DF_API_URL:-https://api.openai.com/v1/chat/completions}"},
      {"name": "api_key", "value": "${DF_API_KEY:-YOUR_API_KEY_HERE}"},
      {"name": "model_name", "value": "gpt-4o"},
      {"name": "temperature", "value": 0.0}
    ]' >> "$STAGE_LOG" 2>&1
fi

# --- 5. E1-fill: new 3a/3b tasks on the 3 agent methods ---
log "E1-fill: new 3a/3b on dataflow_agent/mcp_only/direct_cc ..."
python3 -m runner.batch_runner \
  --experiment-id E1 \
  --tasks 3a_long_doc_summary,3b_text_to_qa_chain \
  --methods dataflow_agent,mcp_only,direct_cc \
  --reps 5 --timeout-sec 600 >> "$STAGE_LOG" 2>&1
log "E1-fill done."

# --- 6. E3-v2: new v2 skill on all 12 tasks, 5 reps ---
log "E3-v2: df_agent_v2 on all 12 tasks, 5 reps ..."
python3 -m runner.batch_runner \
  --experiment-id E1 \
  --tasks all \
  --methods df_agent_v2 \
  --reps 5 --timeout-sec 600 >> "$STAGE_LOG" 2>&1
log "E3-v2 done."

# --- 7. E2 ablation ---
log "E2 ablation: 4 ablations x 12 tasks x 3 reps ..."
python3 -m runner.batch_runner \
  --experiment-id E2_ablation \
  --tasks all \
  --methods ablation \
  --reps 3 --timeout-sec 600 >> "$STAGE_LOG" 2>&1
log "E2 ablation done."

# --- 8. final aggregate ---
log "final aggregation ..."
python3 analysis/aggregate.py --experiment-id E1 >> "$STAGE_LOG" 2>&1
python3 analysis/task_breakdown.py E1 >> "$STAGE_LOG" 2>&1
python3 analysis/failures.py E1 >> "$STAGE_LOG" 2>&1
python3 analysis/aggregate.py --experiment-id E2_ablation >> "$STAGE_LOG" 2>&1

log "ALL DONE. Final artifacts under analysis/E1/ and analysis/E2_ablation/."
