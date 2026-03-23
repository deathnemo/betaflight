#!/usr/bin/env bash
# Применить CLI diff к полётнику (после прошивки, когда появился COM).
# Использование: ./scripts/betaflight-cli-apply.sh -d COM3 -f ~/my/diff.txt
#   или:        ./scripts/betaflight-cli-apply.sh -d 3 -f diff.txt
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEVICE=""
FILE=""

usage() {
    echo "Usage: $0 -d COMn|n|/dev/ttyS* -f path/to/cli-diff.txt" >&2
    exit 1
}

while getopts ":d:f:h" opt; do
    case "$opt" in
        d) DEVICE="$OPTARG" ;;
        f) FILE="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [[ -z "$DEVICE" || -z "$FILE" ]]; then
    usage
fi

# MSYS: C:/... надёжнее как /c/... (иначе бывает Permission denied при open в Python)
if [[ -n "${MSYSTEM:-}" && "$FILE" =~ ^[A-Za-z]:/ ]]; then
    drive="${FILE:0:1}"
    FILE="/${drive,,}/${FILE:3}"
fi

# Наличие файла проверяет Python (_resolve_cli_file_path: /c/... и C:/...)

if [[ "$DEVICE" =~ ^[Cc][Oo][Mm]([0-9]+)$ ]]; then
    DEVICE="${BASH_REMATCH[1]}"
fi

exec python "$ROOT/scripts/betaflight-cli-apply.py" --device "$DEVICE" --file "$FILE"
