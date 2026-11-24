"""Read rendering overrides from the environment or the caller's marker directory."""

import os


def marker_text(directory, filename):
    try:
        with open(os.path.join(directory, filename)) as marker:
            return marker.read().strip() or None
    except (OSError, UnicodeError):
        return None


def float_option(directory, env_name, filename, default=None):
    value = os.environ.get(env_name)
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    value = marker_text(directory, filename)
    if value is not None:
        try:
            return float(value)
        except ValueError:
            pass
    return default


def flag_present(directory, env_name, filename):
    """Presence flags also accept an empty file or a nonempty environment value of '0'."""
    return bool(os.environ.get(env_name)) or os.path.exists(os.path.join(directory, filename))
