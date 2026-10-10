#!/bin/bash
# usage: run_gptr.sh q1
cd "$(dirname "$0")"
export TMPDIR=${TMPDIR:-/tmp} OPENAI_API_KEY="$TOKENHARBOR_API_KEY" OPENAI_BASE_URL=https://tokenharbor.ai/v1
export FAST_LLM=openai:mimo-v2.5:free SMART_LLM=openai:mimo-v2.5:free STRATEGIC_LLM=openai:mimo-v2.5:free
export RETRIEVER=duckduckgo CONTEXT_FILTER=keyword EMBEDDING=openai:text-embedding-3-small
for q in "$@"; do
  timeout 570 ${GPTR_PYTHON:-python} run_gptr.py $q > gptr_$q.out 2> gptr_$q.err; echo "$q rc=$?" | tee -a gptr_rc.txt; cat gptr_$q.out
done
