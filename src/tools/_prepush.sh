#!/bin/bash
# Scoped particle continuity, PC work budget, evidence and foreground power checks.
set -eu
cd "$(dirname "$0")/../.."
python -B src/tools/release_decision.py
python -B src/tools/test_material_release.py
python -B src/tools/check_flow_release.py
python -B src/tools/test_neck_geometry.py
python -B src/tools/test_stream_aperture.py
python -B src/tools/test_receiving_surface.py
python -B src/tools/test_release_decision.py
python -B src/tools/test_pixel_flow_review.py
python -B src/tools/test_stream_review_metrics.py
python -B src/tools/test_flow_release.py
python -B src/tools/test_particle_continuity.py
python -B src/tools/test_particle_material.py
python -B src/tools/test_flare_occlusion.py
python -B src/tools/test_splash_occlusion.py
python -B src/tools/test_landing_spread.py
python -B src/tools/test_splash_colours.py
python -B src/tools/test_splash_tint_batch.py
python -B src/tools/test_splash33_impulse.py
python -B src/tools/test_particle_release.py
python -B src/tools/test_stream_motion_shape.py
python -B src/tools/test_motion_shape_release.py
python -B src/tools/test_short_grain.py
python -B src/tools/test_short_grain_release.py
python -B src/tools/test_grain_style.py
python -B src/tools/test_ui_polish.py
python -B src/tools/test_keep_awake.py
python -B src/tools/verify_keep_awake_runtime.py
exec python -B src/tools/run_regression.py --profile core "$@"
