#!/bin/bash
# This appearance patch uses the scoped MuMu review.
set -eu
cd "$(dirname "$0")/../.."
exec python -B src/tools/check_visual_review.py --review _temp/visual-review/latest.json "$@"
