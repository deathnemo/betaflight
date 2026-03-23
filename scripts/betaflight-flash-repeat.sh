#!/usr/bin/env bash
# Массовая прошивка одного CONFIG по DFU: один раз сборка, затем по Enter — следующая плата.
# Использование: ./scripts/betaflight-flash-repeat.sh -c CONFIG -d COMn [-o \"OPT1 OPT2\"] [-P diff.txt]
#   COMn — номер порта Windows (как в диспетчере устройств), для MSYS2: COM3 -> /dev/ttyS2
#   -P — после каждой успешной прошивки применить CLI diff (вывод diff all из Configurator).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CONFIG=""
COM=""
OPTIONS=""
PRESET=""

usage() {
    echo "Usage: $0 -c CONFIG -d COMn [-o \"OPT1 OPT2\"] [-P cli-diff.txt]" >&2
    echo "  Сначала полная сборка (fwo), затем цикл: Enter — dfu_flash на тот же CONFIG." >&2
    echo "  -P — после прошивки автоматически накатить пресет (через 2 с, без Enter)." >&2
    exit 1
}

while getopts ":c:d:o:P:h" opt; do
    case "$opt" in
        c) CONFIG="$OPTARG" ;;
        d) COM="$OPTARG" ;;
        o) OPTIONS="$OPTARG" ;;
        P) PRESET="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [[ -z "$CONFIG" || -z "$COM" ]]; then
    usage
fi

if ! [[ "$COM" =~ ^[0-9]+$ ]]; then
    echo "COM должен быть числом (например 3 для COM3)." >&2
    exit 1
fi

if [[ -n "$PRESET" && ! -f "$PRESET" ]]; then
    echo "Файл пресета не найден: $PRESET" >&2
    exit 1
fi

SERIAL_DEVICE="/dev/ttyS$((COM - 1))"

make_args=(CONFIG="$CONFIG")
if [[ -n "$OPTIONS" ]]; then
    make_args+=(OPTIONS="$OPTIONS")
fi

echo "=== build (fwo, один раз) ==="
make fwo "${make_args[@]}"

n=1
while true; do
    echo ""
    echo "[$n] Подключите полётник к USB (тот же COM$COM). Enter — прошить, Ctrl+C — выход."
    read -r
    echo "=== dfu_flash ==="
    make dfu_flash "${make_args[@]}" SERIAL_DEVICE="$SERIAL_DEVICE"
    if [[ -n "$PRESET" ]]; then
        echo ""
        echo "=== CLI preset ($PRESET) — пауза 2 с, затем автоматически применить на COM$COM ==="
        sleep 2
        echo "=== betaflight-cli-apply ==="
        "$ROOT/scripts/betaflight-cli-apply.sh" -d "$COM" -f "$PRESET"
    fi
    n=$((n + 1))
done
