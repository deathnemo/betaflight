#!/usr/bin/env python3
"""
Send MSP_REBOOT with MSP_REBOOT_BOOTLOADER_ROM so the FC jumps to the STM32 USB DFU
bootloader (no BOOT button). Expects a Betaflight MSP port (usually USB VCP), 115200 8N1.

MSYS2 maps Windows COMn to /dev/ttyS(n-1). termios/pyserial config on that path often fails
with EINVAL; we use Windows `mode COMn` then a raw write to /dev/ttyS* (no termios).
"""
from __future__ import annotations

import errno
import os
import re
import shutil
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

try:
    import termios
except ImportError:
    termios = None  # type: ignore[assignment]

try:
    import serial as serial_mod
except ImportError:
    serial_mod = None  # type: ignore[assignment]

# MSP v1: MSP_REBOOT (68) + payload 1 = MSP_REBOOT_BOOTLOADER_ROM
def _msp_reboot_rom_packet() -> bytes:
    size = 1
    cmd = 68
    payload = bytes([1])
    crc = size ^ cmd ^ payload[0]
    return bytes([0x24, 0x4D, 0x3C, size, cmd]) + payload + bytes([crc])


def _normalize_serial_arg(arg: str) -> str:
    """
    Win32/MSYS: COM3 иногда приходит как /??/COM3 или //./COM3 — os.path.exists тогда ложный.
    """
    s = arg.strip().strip('"').strip("'")
    s = s.replace("\\", "/")
    if s.startswith("/??/"):
        s = s[4:]
    if s.startswith("//./"):
        s = s[4:]
    return s


def _coerce_msys_tty_path(dev: str) -> str:
    """Под MSYS2 COMn или «3» приводим к /dev/ttyS(n-1) для mode+raw."""
    if not (os.environ.get("MSYSTEM") or sys.platform.startswith("msys")):
        return dev
    m = re.match(r"^COM(\d+)$", dev, re.I)
    if m:
        return f"/dev/ttyS{int(m.group(1)) - 1}"
    if re.match(r"^\d+$", dev):
        return f"/dev/ttyS{int(dev) - 1}"
    return dev


def _msys_ttys_to_com(dev: str) -> str | None:
    """MSYS2: /dev/ttySn -> COM(n+1)."""
    m = re.match(r"^/dev/ttyS(\d+)$", dev)
    if not m:
        return None
    return f"COM{int(m.group(1)) + 1}"


def _is_msys2_ttys(dev: str) -> bool:
    if not dev.startswith("/dev/ttyS"):
        return False
    if os.environ.get("MSYSTEM"):
        return True
    return sys.platform.startswith("msys")


def _resolve_cmd_exe() -> str:
    """
    MSYS2 Python cannot exec Win32 paths like C:\\Windows\\...; use POSIX /c/... or PATH.
    """
    for p in ("/c/Windows/System32/cmd.exe", "/c/WINDOWS/System32/cmd.exe"):
        if os.path.isfile(p):
            return p
    cs = os.environ.get("COMSPEC", "")
    if cs.startswith("/") and os.path.isfile(cs):
        return cs
    if len(cs) >= 3 and cs[1] == ":" and cs[2] in "\\/":
        drive = cs[0].lower()
        rest = cs[3:].replace("\\", "/")
        np = f"/{drive}/{rest}"
        if os.path.isfile(np):
            return np
    w = shutil.which("cmd.exe") or shutil.which("cmd")
    if w:
        return w
    return "cmd.exe"


def _open_serial_fd(dev: str) -> int:
    """Avoid blocking forever on open/write (common with /dev/ttyS* under MSYS2)."""
    flags = os.O_RDWR
    if hasattr(os, "O_NOCTTY"):
        flags |= os.O_NOCTTY
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = os.open(dev, flags)
    if fcntl is not None and hasattr(os, "O_NONBLOCK"):
        try:
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        except OSError:
            pass
    return fd


def _write_all(fd: int, data: bytes, deadline_s: float = 3.0) -> None:
    """Write full buffer; handle non-blocking EAGAIN with short retries."""
    t0 = time.monotonic()
    mv = memoryview(data)
    while mv:
        try:
            n = os.write(fd, mv)
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            if time.monotonic() - t0 > deadline_s:
                raise TimeoutError("serial write timed out") from e
            time.sleep(0.01)
            continue
        mv = mv[n:]


def _send_via_winmode_and_raw(dev: str, packet: bytes) -> None:
    """
    Configure COM via Windows `mode`, then write bytes through MSYS /dev/ttyS* without termios.
    """
    com = _msys_ttys_to_com(dev)
    if com:
        cmdexe = _resolve_cmd_exe()
        try:
            subprocess.run(
                [cmdexe, "/c", f"mode {com} BAUD=115200 PARITY=N DATA=8 STOP=1"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            pass
    try:
        fd = _open_serial_fd(dev)
    except OSError:
        # Нативный Python на Windows не открывает /dev/ttyS* — шлём через pyserial на COM.
        if com and serial_mod is not None:
            _send_via_pyserial(com, packet)
            return
        raise
    try:
        _write_all(fd, packet)
    finally:
        os.close(fd)


def _send_via_pyserial(dev: str, packet: bytes) -> None:
    if serial_mod is None:
        raise RuntimeError("pyserial not installed")
    ser = serial_mod.Serial(
        port=dev,
        baudrate=115200,
        bytesize=serial_mod.EIGHTBITS,
        parity=serial_mod.PARITY_NONE,
        stopbits=serial_mod.STOPBITS_ONE,
        timeout=2,
        write_timeout=2,
    )
    try:
        ser.write(packet)
        ser.flush()
    finally:
        ser.close()


def _configure_serial(fd: int) -> None:
    assert termios is not None
    attrs = termios.tcgetattr(fd)
    attrs[0] &= ~(
        getattr(termios, "IGNBRK", 0)
        | getattr(termios, "BRKINT", 0)
        | getattr(termios, "PARMRK", 0)
        | getattr(termios, "ISTRIP", 0)
        | getattr(termios, "INLCR", 0)
        | getattr(termios, "IGNCR", 0)
        | getattr(termios, "ICRNL", 0)
        | getattr(termios, "IXON", 0)
        | getattr(termios, "IXOFF", 0)
        | getattr(termios, "IXANY", 0)
    )
    attrs[1] &= ~getattr(termios, "OPOST", 0)
    attrs[2] &= ~(termios.CSIZE | termios.PARENB)
    attrs[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[2] &= ~termios.CSTOPB
    attrs[3] &= ~(
        termios.ECHO
        | getattr(termios, "ECHONL", 0)
        | termios.ICANON
        | termios.ISIG
        | getattr(termios, "IEXTEN", 0)
    )
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.cfsetispeed(attrs, termios.B115200)
    termios.cfsetospeed(attrs, termios.B115200)
    termios.tcsetattr(fd, termios.TCSAFLUSH, attrs)


def _send_via_termios(dev: str, packet: bytes) -> None:
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY)
    try:
        _configure_serial(fd)
        os.write(fd, packet)
    finally:
        os.close(fd)


def _send_raw(dev: str, packet: bytes) -> None:
    fd = _open_serial_fd(dev)
    try:
        _write_all(fd, packet)
    finally:
        os.close(fd)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: msp-reboot-bootloader.py <serial_device>", file=sys.stderr)
        return 2
    dev = _coerce_msys_tty_path(_normalize_serial_arg(sys.argv[1]))
    # Не полагаемся на os.path.exists для COM и /dev/ttyS* (VCP может вести себя иначе).
    packet = _msp_reboot_rom_packet()

    # 1) MSYS2 + /dev/ttyS*: Windows `mode` + raw write (pyserial/termios EINVAL on this path)
    if _is_msys2_ttys(dev):
        try:
            _send_via_winmode_and_raw(dev, packet)
            return 0
        except (OSError, TimeoutError) as e:
            print(f"msp-reboot-bootloader: mode+raw failed: {e}", file=sys.stderr)
            print(
                "Hint: close Betaflight Configurator / other tools using this COM port.",
                file=sys.stderr,
            )
            return 1

    # 2) pyserial — typical Linux / native Windows COM
    if serial_mod is not None:
        try:
            _send_via_pyserial(dev, packet)
            return 0
        except Exception as e:
            print(f"msp-reboot-bootloader: pyserial failed: {e}", file=sys.stderr)
            print(
                "Hint: close Betaflight Configurator / other tools using this COM port.",
                file=sys.stderr,
            )
            return 1

    # 3) termios — Linux
    if termios is not None:
        try:
            _send_via_termios(dev, packet)
            return 0
        except termios.error as e:
            if e.args and e.args[0] != 22:
                print(f"msp-reboot-bootloader: termios failed: {e}", file=sys.stderr)
                return 1
        except OSError as e:
            print(f"msp-reboot-bootloader: {dev}: {e}", file=sys.stderr)
            return 1

    # 4) raw write
    try:
        _send_raw(dev, packet)
        return 0
    except OSError as e:
        print(f"msp-reboot-bootloader: raw write failed: {e}", file=sys.stderr)
        print(
            "Hint: close Betaflight Configurator / other tools using this COM port.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
