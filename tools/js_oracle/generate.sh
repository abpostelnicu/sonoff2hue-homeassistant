#!/usr/bin/env bash
# Regenerates tests/engine/fixtures/js_picker.json from the .NET app's own web UI (README.md).
# Usage, from anywhere: tools/js_oracle/generate.sh <checkout of abpostelnicu/sonoff2hue>
set -euo pipefail

if [ $# -ne 1 ] || [ ! -f "$1/src/Sonoff2Hue.Frontend/wwwroot/js/targets.js" ]; then
  echo "usage: $0 <checkout of abpostelnicu/sonoff2hue, the .NET app>" >&2
  exit 2
fi
app=$(cd "$1" && pwd)
cd "$(dirname "$0")/../.."
python=${PYTHON:-.venv/bin/python}
version=$(git -C "$app" describe --always --dirty --tags)
cases=$(mktemp "${TMPDIR:-/tmp}/picker_cases.XXXXXX.json")
trap 'rm -f "$cases"' EXIT

"$python" tools/js_oracle/picker_cases.py "$cases"
node tools/js_oracle/oracle.mjs "$app" "$cases" tests/engine/fixtures/js_picker.json "sonoff2hue $version"
