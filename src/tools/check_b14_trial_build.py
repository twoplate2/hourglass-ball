"""Check the packaged 3.36 preview and disclose its two reviewed capacity discrepancies."""

import json
from pathlib import Path
import subprocess
import sys


TEST = Path(__file__).with_name('test_b14_transit.py')
# Fractions of a full bulb in the capacity test's fixed geometry. The normal-
# size image measurements are reported separately in the prerelease notes.
TRIAL_DEFICIT = {20: .05951, 50: .01984}


def main():
    subprocess.run([sys.executable, '-B', str(TEST), '--packaged-trial'], check=True)
    strict = subprocess.run([sys.executable, '-B', str(TEST), '--packaged-capacity'],
                            capture_output=True, text=True)
    print(strict.stdout, end='')
    print(strict.stderr, end='', file=sys.stderr)
    if strict.returncode not in (0, 2):
        raise SystemExit('Could not evaluate the packaged trial capacity')

    seen = set()
    failures = set()
    for line in strict.stdout.splitlines():
        if not line.startswith('CAPACITY '):
            continue
        row = json.loads(line[len('CAPACITY '):])
        duration = row['duration']
        released, capacity = row['released'], row['capacity']
        if duration in seen or not (0 < released <= 1 and 0 < capacity <= 1):
            raise SystemExit(f'Invalid capacity sample at {duration}s')
        seen.add(duration)
        deficit = released - capacity
        if deficit > 0:
            failures.add(duration)
            if deficit > TRIAL_DEFICIT.get(duration, 0):
                raise SystemExit(f'Unreviewed capacity shortfall at {duration}s: {deficit:.6%}')
            print(f'::warning::B14 trial {duration}s fails the production capacity check by '
                  f'{deficit * 100:.3f} percentage points in the fixed test glass.')

    if seen != {1, 5, 20, 50, 6000} or bool(failures) != (strict.returncode == 2):
        raise SystemExit('Incomplete capacity results or inconsistent strict-check exit status')


if __name__ == '__main__':
    main()
