#!/bin/bash
# Scoped PC contact-engine and appearance evidence, not a full/FPS regression.
set -eu
cd "$(dirname "$0")/../.."
exec python -B src/tools/check_pymunk_release.py "$@"
