#!/usr/bin/env bash
# Regenerates the fixtures that pin the port to the .NET app's behavior (README.md).
# Usage, from anywhere: tools/dotnet_oracle/generate.sh <checkout of abpostelnicu/sonoff2hue>
set -euo pipefail

if [ $# -ne 1 ] || [ ! -f "$1/src/Sonoff2Hue/Sonoff2Hue.csproj" ]; then
  echo "usage: $0 <checkout of abpostelnicu/sonoff2hue, the .NET app>" >&2
  exit 2
fi
app=$(cd "$1" && pwd)
cd "$(dirname "$0")/../.."
python=${PYTHON:-.venv/bin/python}
version=$(git -C "$app" describe --always --dirty --tags)
cases=$(mktemp "${TMPDIR:-/tmp}/engine_cases.XXXXXX.json")
trap 'rm -f "$cases"; rm -rf tools/dotnet_oracle/bin tools/dotnet_oracle/obj' EXIT

PYTHONPATH=. "$python" tools/dotnet_oracle/engine_cases.py "$cases"
run=(dotnet run --project tools/dotnet_oracle -p:Sonoff2HueProject="$app/src/Sonoff2Hue/Sonoff2Hue.csproj" --)
"${run[@]}" text
"${run[@]}" engine "$cases" tests/engine/fixtures/dotnet_engine.json "sonoff2hue $version"
