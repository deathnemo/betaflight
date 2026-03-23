#!/usr/bin/env python3
"""
Применить сохранённый CLI (вывод «diff» / «diff all») к Betaflight по USB VCP.

На MSYS2 сырой /dev/ttyS* через os.open часто даёт Permission denied; предпочтительно
pyserial с COMn (Win32). Если pyserial нет — mode + сырой fd + select. На Linux — pyserial.

pyserial:  python -m pip install --user pyserial   (на MSYS UCRT64 рекомендуется)
"""
from __future__ import annotations

import argparse
import errno
import inspect
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
    import select as select_mod
except ImportError:
    select_mod = None  # type: ignore[assignment]


def _cli_file_candidates(path: str) -> list[str]:
    """Варианты пути к файлу (MSYS: и /c/..., и C:/...)."""
    raw = path.strip().strip('"').strip("'")
    raw = os.path.expanduser(raw)
    raw = raw.replace("\\", "/")
    candidates: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        if not p:
            return
        p = p.replace("\\", "/")
        if p not in seen:
            seen.add(p)
            candidates.append(p)

    add(raw)
    if not os.path.isabs(raw):
        add(os.path.normpath(os.path.join(os.getcwd(), raw)))

    if os.environ.get("MSYSTEM"):
        if len(raw) >= 3 and raw[1] == ":" and raw[2] == "/":
            add(f"/{raw[0].lower()}/{raw[3:]}")
        if len(raw) >= 4 and raw[0] == "/" and raw[2] == "/" and raw[1].isalpha():
            add(f"{raw[1].upper()}:{raw[3:]}")

    return candidates


def _resolve_cli_file_path(path: str) -> str:
    """Первый существующий путь из кандидатов; иначе FileNotFoundError со списком."""
    for c in _cli_file_candidates(path):
        try:
            if os.path.isfile(c):
                return c
        except OSError:
            pass
    cands = _cli_file_candidates(path)
    raise FileNotFoundError(
        "Файл не найден. Убедитесь, что путь верный и файл существует. "
        f"Пробовали: {', '.join(repr(c) for c in cands)}"
    )


def resolve_serial_port(arg: str) -> str:
    """COM3 / 3 / /dev/ttyS2 / /dev/ttyACM0."""
    arg = arg.strip()
    if arg.startswith("/dev/"):
        return arg
    m = re.match(r"^COM(\d+)$", arg, re.I)
    if m:
        n = int(m.group(1))
        if os.name == "nt" or sys.platform.startswith("win"):
            return f"COM{n}"
        return f"/dev/ttyS{n - 1}"
    if re.match(r"^\d+$", arg):
        n = int(arg)
        if os.name == "nt" or sys.platform.startswith("win"):
            return f"COM{n}"
        return f"/dev/ttyS{n - 1}"
    return arg


def _msys_ttys_to_com(dev: str) -> str | None:
    m = re.match(r"^/dev/ttyS(\d+)$", dev)
    if not m:
        return None
    return f"COM{int(m.group(1)) + 1}"


def _is_msys_ttys(dev: str) -> bool:
    if not dev.startswith("/dev/ttyS"):
        return False
    if os.environ.get("MSYSTEM"):
        return True
    return sys.platform.startswith("msys")


def _resolve_cmd_exe() -> str:
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
    flags = os.O_RDWR | os.O_NOCTTY
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(dev, flags)
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EPERM) and (
            dev.startswith("/dev/") or "tty" in dev.lower()
        ):
            raise OSError(
                errno.EACCES,
                f"{e.strerror}: порт {dev!r} — закройте Betaflight Configurator, "
                "терминалы и другие программы, занявшие COM.",
                dev,
            ) from e
        raise
    if fcntl is not None and hasattr(os, "O_NONBLOCK"):
        try:
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        except OSError:
            pass
    return fd


def _write_all(fd: int, data: bytes, deadline_s: float = 5.0) -> None:
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


def _run_mode_com(dev: str) -> None:
    com = _msys_ttys_to_com(dev)
    if not com:
        return
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


def _drain_fd(fd: int, max_total: float = 2.0, idle: float = 0.45) -> bytes:
    """Считать всё, что успело прийти за max_total с, пока нет данных idle с."""
    if select_mod is None:
        time.sleep(min(0.3, max_total))
        try:
            return os.read(fd, 65536)
        except BlockingIOError:
            return b""
    buf = b""
    t_start = time.monotonic()
    last_data = time.monotonic()
    while time.monotonic() - t_start < max_total:
        r, _, _ = select_mod.select([fd], [], [], idle)
        if not r:
            if buf and time.monotonic() - last_data >= idle:
                break
            continue
        try:
            chunk = os.read(fd, 8192)
        except BlockingIOError:
            break
        if not chunk:
            break
        buf += chunk
        last_data = time.monotonic()
    return buf


def _looks_like_cli_prompt(buf: bytes) -> bool:
    """Строка приглашения Betaflight CLI: выводится как «# » (см. cliPrint в cli.c)."""
    if not buf:
        return False
    clean = re.sub(rb"\x1b\[[0-9;]*m", b"", buf)
    tail = clean[-4096:] if len(clean) > 4096 else clean
    if tail.rstrip().endswith(b"#"):
        return True
    for line in reversed(tail.splitlines()):
        s = line.rstrip()
        if not s:
            continue
        if s.endswith(b"#"):
            return True
    return False


def _msp_request_cli_fd(fd: int) -> None:
    """Только байт 0x23. В msp_serial.c следующий байт сбрасывает MSP_PENDING_CLI."""
    _write_all(fd, b"#")
    time.sleep(0.2)


def _msp_request_cli_serial(ser) -> None:
    """То же для pyserial."""
    ser.write(b"#")
    ser.flush()
    time.sleep(0.2)


def _wait_prompt_fd(fd: int, timeout: float = 25.0, prefix: bytes = b"") -> None:
    buf = prefix
    t0 = time.monotonic()
    if _looks_like_cli_prompt(buf):
        return
    while time.monotonic() - t0 < timeout:
        if select_mod is None:
            time.sleep(0.12)
            try:
                buf += os.read(fd, 8192)
            except BlockingIOError:
                pass
        else:
            r, _, _ = select_mod.select([fd], [], [], 0.25)
            if r:
                try:
                    buf += os.read(fd, 8192)
                except BlockingIOError:
                    pass
        if _looks_like_cli_prompt(buf):
            return
        time.sleep(0.02)
    raise TimeoutError(
        "не дождались приглашения CLI (#). Закройте Configurator, проверьте COM, "
        "или попробуйте --no-enter-cli если уже в CLI."
    )


def _load_commands(path: str, append_save: bool) -> list[str]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        lines = f.readlines()
    commands: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        commands.append(line)
    if append_save:
        low = [c.strip().lower() for c in commands]
        if not any(x == "save" for x in low):
            commands.append("save")
    if not commands:
        raise ValueError("нет команд для отправки (пустой файл или только строки с #)")
    return commands


def apply_file_msys(
    port: str,
    commands: list[str],
    enter_cli_first: bool,
) -> None:
    _run_mode_com(port)
    fd = _open_serial_fd(port)
    try:
        # USB CDC после перезагрузки может отдавать вывод с задержкой
        time.sleep(3.0)
        buf = _drain_fd(fd, max_total=3.0)
        if _looks_like_cli_prompt(buf):
            pass
        elif enter_cli_first:
            _msp_request_cli_fd(fd)
            buf += _drain_fd(fd, max_total=2.0)
            if not _looks_like_cli_prompt(buf):
                _msp_request_cli_fd(fd)
                buf += _drain_fd(fd, max_total=2.0)
            if not _looks_like_cli_prompt(buf):
                _wait_prompt_fd(fd, timeout=22.0, prefix=buf)
        else:
            if not _looks_like_cli_prompt(buf):
                _wait_prompt_fd(fd, timeout=22.0, prefix=buf)

        for cmd in commands:
            _write_all(fd, (cmd + "\r\n").encode("utf-8", errors="replace"))
            time.sleep(0.04)
            _drain_fd(fd, max_total=1.2)
            if cmd.strip().lower() == "save":
                time.sleep(1.2)
                break
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _serial_open_pyserial(port: str, baud: int):
    """Открыть COM с параметрами, совместимыми с USB CDC Betaflight (без лишних IOCTL)."""
    import serial

    kw: dict = dict(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.15,
        write_timeout=5,
        dsrdtr=False,
        rtscts=False,
        xonxoff=False,
    )
    sig = inspect.signature(serial.Serial.__init__)
    if "exclusive" in sig.parameters:
        kw["exclusive"] = True
        try:
            return serial.Serial(**kw)
        except Exception:
            del kw["exclusive"]
    return serial.Serial(**kw)


def _safe_serial_reset_buffers(ser) -> None:
    """reset_* вызывает ClearCommError на части драйверов Windows — игнорируем."""
    try:
        ser.reset_input_buffer()
    except (OSError, PermissionError):
        pass
    try:
        ser.reset_output_buffer()
    except (OSError, PermissionError):
        pass


def _serial_in_waiting_safe(ser) -> int:
    """in_waiting на Win32 вызывает ClearCommError у части USB CDC — не роняем скрипт."""
    try:
        n = ser.in_waiting
        return int(n) if n else 0
    except Exception:
        return 0


def _serial_read_safe(ser, n: int) -> bytes:
    try:
        return ser.read(n)
    except Exception:
        return b""


def apply_file_pyserial(
    port: str,
    commands: list[str],
    baud: int,
    enter_cli_first: bool,
) -> None:
    try:
        import serial
    except ImportError:
        print("Установите pyserial:  python -m pip install --user pyserial", file=sys.stderr)
        raise

    ser = _serial_open_pyserial(port, baud)
    try:
        time.sleep(3.0)
        _safe_serial_reset_buffers(ser)

        def drain_s(max_t: float = 2.0) -> bytes:
            out = b""
            t0 = time.monotonic()
            idle = time.monotonic()
            while time.monotonic() - t0 < max_t:
                n = _serial_in_waiting_safe(ser)
                if n:
                    out += _serial_read_safe(ser, min(n, 65536))
                    idle = time.monotonic()
                else:
                    if out and time.monotonic() - idle > 0.4:
                        break
                    time.sleep(0.02)
            return out

        def wait_prompt_s(prefix: bytes = b"") -> None:
            buf = prefix
            t0 = time.monotonic()
            if _looks_like_cli_prompt(buf):
                return
            while time.monotonic() - t0 < 25.0:
                nw = _serial_in_waiting_safe(ser)
                if nw:
                    buf += _serial_read_safe(ser, nw)
                    if _looks_like_cli_prompt(buf):
                        return
                else:
                    time.sleep(0.05)
            raise TimeoutError(
                "не дождались приглашения CLI (#). Закройте Configurator, проверьте порт."
            )

        buf = drain_s(3.0)
        if _looks_like_cli_prompt(buf):
            pass
        elif enter_cli_first:
            _msp_request_cli_serial(ser)
            buf += drain_s(2.0)
            if not _looks_like_cli_prompt(buf):
                _msp_request_cli_serial(ser)
                buf += drain_s(2.0)
            if not _looks_like_cli_prompt(buf):
                wait_prompt_s(buf)
        else:
            if not _looks_like_cli_prompt(buf):
                wait_prompt_s(buf)

        for cmd in commands:
            ser.write((cmd + "\r\n").encode("utf-8", errors="replace"))
            ser.flush()
            time.sleep(0.04)
            drain_s()
            if cmd.strip().lower() == "save":
                time.sleep(1.0)
                break
    finally:
        try:
            ser.close()
        except Exception:
            pass


def apply_file(
    port: str,
    path: str,
    baud: int,
    enter_cli_first: bool,
    append_save: bool,
) -> None:
    commands = _load_commands(path, append_save)

    com = _msys_ttys_to_com(port)
    # MSYS: Win32 COM через pyserial обходит EACCES на /dev/ttyS*
    if _is_msys_ttys(port) and com:
        try:
            import serial  # noqa: F401
        except ImportError:
            print(
                "Подсказка: установите pyserial (python -m pip install pyserial). "
                "Без него на MSYS порт /dev/ttyS* часто даёт [Errno 13] Permission denied.",
                file=sys.stderr,
            )
            apply_file_msys(port, commands, enter_cli_first)
            return
        try:
            apply_file_pyserial(com, commands, baud, enter_cli_first)
            return
        except TimeoutError:
            raise
        except Exception as e:
            # SerialException/ValueError с текстом «ClearCommError failed (PermissionError…)» и т.п.
            print(
                f"pyserial не подошёл ({type(e).__name__}): {e}. "
                "Пробуем сырой порт /dev/ttyS* ...",
                file=sys.stderr,
            )
            apply_file_msys(port, commands, enter_cli_first)
            return

    if _is_msys_ttys(port):
        apply_file_msys(port, commands, enter_cli_first)
    else:
        apply_file_pyserial(port, commands, baud, enter_cli_first)


def main() -> int:
    ap = argparse.ArgumentParser(description="Применить CLI diff к Betaflight по serial.")
    ap.add_argument(
        "-d",
        "--device",
        required=True,
        help="COM: 3, COM3 или /dev/ttyS2, /dev/ttyACM0",
    )
    ap.add_argument("-f", "--file", required=True, help="Файл с командами CLI (diff / diff all)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument(
        "--no-enter-cli",
        action="store_true",
        help="Не слать # (если уже в чистом CLI)",
    )
    ap.add_argument(
        "--no-save",
        action="store_true",
        help="Не добавлять save в конец",
    )
    args = ap.parse_args()

    port = resolve_serial_port(args.device)
    try:
        cli_file = _resolve_cli_file_path(args.file)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    try:
        apply_file(
            port,
            cli_file,
            args.baud,
            enter_cli_first=not args.no_enter_cli,
            append_save=not args.no_save,
        )
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    except TimeoutError as e:
        print(e, file=sys.stderr)
        return 1
    except OSError as e:
        fn = getattr(e, "filename", None)
        if fn:
            print(f"{e} (path={fn!r})", file=sys.stderr)
        else:
            print(e, file=sys.stderr)
        if e.errno in (errno.EACCES, errno.EPERM):
            if fn and os.path.isfile(str(fn)):
                print(
                    "Проверьте права на файл или укажите путь как /c/Users/... в MSYS.",
                    file=sys.stderr,
                )
        return 1
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    except Exception as e:
        msg = str(e)
        tname = type(e).__name__
        if tname == "SerialException" or "could not open port" in msg.lower() or "configure port" in msg.lower():
            print(f"serial: {e}", file=sys.stderr)
            print(
                "Закройте Betaflight Configurator и другие программы, занявшие порт.",
                file=sys.stderr,
            )
            return 1
        print(f"{tname}: {e}", file=sys.stderr)
        return 1

    print("Готово. Контроллер мог перезагрузиться после save.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
