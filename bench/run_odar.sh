#!/bin/bash
# usage: run_odar.sh q1   (one question per call; each fits under the 600s job cap)
cd "$(dirname "$0")/.."
export TMPDIR=${TMPDIR:-/tmp} ANTHROPIC_API_KEY="$TOKENHARBOR_API_KEY" ODAR_ANTHROPIC_BASE_URL=https://tokenharbor.ai ODAR_ANTHROPIC_MODEL=mimo-v2.5:free
B=$PWD/bench
for q in "$@"; do
  Q=$(python3 -c "import json;print(json.load(open('$B/questions.json'))['$q'])")
  rm -rf $B/odar_$q $B/odar_$q.db*; mkdir -p $B/odar_$q
  s=$(date +%s.%N)
  timeout 585 ${PYTHON:-python} bench/odar_ddgs_only.py --db $B/odar_$q.db run --model llm --llm-model mimo-v2.5:free --max-iterations 4 --max-search 8 --max-fetch 8 --max-model-calls 30 --timeout 540 --output-dir $B/odar_$q --json "$Q" > $B/odar_$q.json 2> $B/odar_$q.err
  rc=$?; e=$(date +%s.%N)
  echo "$q rc=$rc secs=$(python3 -c "print(round($e-$s,1))")" | tee -a $B/odar_times.txt
done
