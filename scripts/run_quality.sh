#!/usr/bin/env bash
set -euo pipefail

python -m compileall -q src/dynosai_flow
python scripts/check_repository.py
python -m pytest
