#!/usr/bin/env bash
# Сборка/прошивка Betaflight: TARGET или CONFIG, OPTIONS, режим прошивки.
# Запускайте из MSYS2/WSL/Linux (нужны make, dfu-util/stm32flash по цели).
#
# OPTIONS добавляет -D в gcc. Не дублируйте макросы, которые уже задаёт
# src/main/target/common_pre.h (USE_DSHOT, USE_PINIO, USE_SERIALRX_*, …),
# иначе будет error: "USE_*" redefined [-Werror]. Для обычной сборки часто
# достаточно вызова без -o.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
    echo "Usage: $0 -t TARGET [-o \"OPT1 OPT2\"] [-f none|dfu|serial|stlink] [-d SERIAL_DEVICE]" >&2
    echo "   or: $0 -c CONFIG [-o \"OPT1 OPT2\"] [-f none|dfu|serial|stlink] [-d SERIAL_DEVICE]" >&2
    echo "MSYS2: COMn = /dev/ttyS\$((n-1)); для dfu укажите -d, чтобы MSP перевёл плату в DFU без кнопки BOOT." >&2
    exit 1
}

TARGET=""
CONFIG=""
OPTIONS=""
FLASH="none"
SERIAL_DEVICE=""

while getopts ":t:c:o:f:d:h" opt; do
    case "$opt" in
        t) TARGET="$OPTARG" ;;
        c) CONFIG="$OPTARG" ;;
        o) OPTIONS="$OPTARG" ;;
        f) FLASH="$OPTARG" ;;
        d) SERIAL_DEVICE="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [[ -n "$TARGET" && -n "$CONFIG" ]]; then
    echo "Укажите только -t или -c." >&2
    exit 1
fi
if [[ -z "$TARGET" && -z "$CONFIG" ]]; then
    usage
fi

# Windows COMn -> MSYS2 /dev/ttyS(n-1)
if [[ -n "$SERIAL_DEVICE" ]] && [[ "$SERIAL_DEVICE" =~ ^[Cc][Oo][Mm]([0-9]+)$ ]]; then
    n="${BASH_REMATCH[1]}"
    SERIAL_DEVICE="/dev/ttyS$((n - 1))"
fi

make_args=()
if [[ -n "$TARGET" ]]; then
    make_args+=(TARGET="$TARGET")
fi
if [[ -n "$CONFIG" ]]; then
    make_args+=(CONFIG="$CONFIG")
fi
if [[ -n "$OPTIONS" ]]; then
    make_args+=(OPTIONS="$OPTIONS")
fi

echo "=== build (fwo) ==="
make fwo "${make_args[@]}"

case "$FLASH" in
    none) exit 0 ;;
    dfu)
        echo "=== dfu_flash ==="
        dfu_args=("${make_args[@]}")
        if [[ -n "$SERIAL_DEVICE" ]]; then
            dfu_args+=(SERIAL_DEVICE="$SERIAL_DEVICE")
        fi
        make dfu_flash "${dfu_args[@]}"
        ;;
    serial)
        if [[ -z "$SERIAL_DEVICE" ]]; then
            echo "Для serial нужен -d SERIAL_DEVICE." >&2
            exit 1
        fi
        echo "=== tty_flash ($SERIAL_DEVICE) ==="
        make tty_flash "${make_args[@]}" SERIAL_DEVICE="$SERIAL_DEVICE"
        ;;
    stlink)
        echo "=== st-flash ==="
        make st-flash "${make_args[@]}"
        ;;
    *)
        echo "Неверный -f: $FLASH" >&2
        exit 1
        ;;
esac
