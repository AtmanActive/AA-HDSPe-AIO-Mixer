#!/bin/sh
# Prints the app's version, from aa_hdspe_mixer/__init__.py (the one place it is set).
# Doesn't need Python, so it works in any build container.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "$ROOT/aa_hdspe_mixer/__init__.py"
