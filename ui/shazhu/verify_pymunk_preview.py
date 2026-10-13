"""Check the generated Pymunk preview only; no app or device is started."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from pymunk_grains import (
    OUTPUT, NAME, LEFT_WALL, WIDTH, source_identity, NECK_BOTTOM, draw_frame, grain_bank,
)


def verify(directory):
    record = json.loads((directory / (NAME + "-physics.json")).read_text(encoding="utf-8"))
    cache = directory / (NAME + ".npz")
    assert record["source"] == source_identity(), "Stale source identity"
    assert record["cache_sha256"] == hashlib.sha256(cache.read_bytes()).hexdigest()
    assert record["particle_count_conserved"], "Particle count changed"
    assert record["grain_contact_begins"] > 1000, "No real grain contacts"
    assert record["stream_contact_begins"] > 0, "No contact in the falling stream"
    assert record["source_grains_contacting_lower_region"] > 100, "No source-to-bottom contacts"
    assert not record["prescribed_column_outline"]
    assert not record["artificial_splash_particles"]
    assert not record["seamless_loop"] and record["cut_on_replay"]
    with np.load(cache, allow_pickle=False) as data:
        positions, bank_ids, upper = data["positions"], data["bank_ids"], data["upper"]
    assert positions.shape == (record["frames"], record["grains"], 2)
    assert record["frames"] == record["fps"] * record["seconds"]
    assert len(bank_ids) == len(upper) == record["grains"]
    assert np.isfinite(positions).all(), "Nonfinite trajectory"
    assert positions[:, :, 0].min() > -2 and positions[:, :, 0].max() < 642
    assert positions[:, :, 1].max() < 903, "Grains tunnelled through the floor"
    hopper = positions[(positions[:, :, 1] >= LEFT_WALL[0][1])
                       & (positions[:, :, 1] <= NECK_BOTTOM)]
    left = np.interp(hopper[:, 1], [p[1] for p in LEFT_WALL], [p[0] for p in LEFT_WALL])
    assert np.all(hopper[:, 0] >= left - 2.0), "Grains crossed the left hopper wall"
    assert np.all(hopper[:, 0] <= WIDTH - left + 2.0), "Grains crossed the right hopper wall"
    assert (positions[-1, upper, 1] > NECK_BOTTOM).sum() > 100
    assert (positions[-1, upper, 1] > 760.0).sum() > 100, "No source grains reaching the lower sand"
    assert (np.linalg.norm(positions[-1] - positions[0], axis=1) > 1).sum() > 100
    bank = grain_bank()
    a = np.asarray(draw_frame(positions[0], bank_ids, bank))
    b = np.asarray(draw_frame(positions[-1], bank_ids, bank))
    assert np.any(a[230:850] != b[230:850]), "Static sand stream"
    print(f"Pymunk {record['source']['pymunk']}: {record['grains']} conserved grains")
    print("Source identity, real contacts, source-to-bottom motion, bounds and nonblank rendering PASS")
    print(f"{record['frames']} frames / {record['seconds']} seconds; replay cut explicitly recorded")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    verify(args.output.resolve())
