#!/bin/bash
cd "$(dirname "$0")"
[ -f "${ODAR_BENCH_ENV:-.env}" ] && source "${ODAR_BENCH_ENV:-.env}"
export ANTHROPIC_API_KEY=$TOKENHARBOR_API_KEY ODAR_ANTHROPIC_BASE_URL=https://tokenharbor.ai PYTHONPATH="$(cd ../.. && pwd)"
${PYTHON:-python} score.py ${SYS:-odar2} $1 $2
