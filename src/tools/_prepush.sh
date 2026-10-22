#!/bin/bash
# Scoped material appearance/source and foreground power checks, not FPS QA.
set -eu
cd "$(dirname "$0")/../.."
python -B src/tools/test_material_release.py
python -B src/tools/check_short_grain_release.py
python -B src/tools/test_short_grain.py
python -B src/tools/test_short_grain_release.py
python -B src/tools/test_grain_style.py
python -B src/tools/test_ui_polish.py
python -B src/tools/test_keep_awake.py
python -B src/tools/verify_keep_awake_runtime.py
exec python -B src/tools/run_regression.py --profile core "$@"
