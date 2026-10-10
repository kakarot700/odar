#!/bin/bash
cd "$(dirname "$0")"
export TMPDIR=${TMPDIR:-/tmp} PREFIX=${PREFIX:-odar2} ANTHROPIC_API_KEY=$TOKENHARBOR_API_KEY ODAR_ANTHROPIC_BASE_URL=https://tokenharbor.ai
${PYTHON:-python} run_odar2.py $1 $2 $3
