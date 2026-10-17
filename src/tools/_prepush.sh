#!/bin/bash
# Scoped 3.0 rollback and foreground power checks, not a full/FPS regression.
set -eu
cd "$(dirname "$0")/../.."
python -B src/tools/check_rollback_release.py
python -B src/tools/test_keep_awake.py
python -B src/tools/verify_keep_awake_runtime.py
exec python -B src/tools/run_regression.py --profile core "$@"
