"""Stage 6: host-side serial monitor.

Opens the board's USB/UART port with pyserial, prints everything the board
transmits, and forwards console input back to the board. Used by
``python voltc.py monitor -p COM3``.
"""

from __future__ import annotations

import sys
import time


class MonitorError(Exception):
    """Raised when the monitor cannot run (no pyserial, bad port, ...)."""


def _require_pyserial():
    try:
        import serial  # noqa: F401
        return True
    except ImportError:
        return False


def open_serial(port, baud=9600):
    """Open ``port`` at ``baud`` baud. Raises MonitorError on failure."""
    if not _require_pyserial():
        raise MonitorError(
            "pyserial is required for the serial monitor.\n"
            "Install it with:  python -m pip install pyserial"
        )
    import serial

    try:
        ser = serial.Serial(port, baud, timeout=0.05)
    except serial.SerialException as exc:
        raise MonitorError(f"cannot open {port}: {exc}")
    return ser


def list_ports():
    """Return the names of attached serial ports, or raise MonitorError."""
    if not _require_pyserial():
        raise MonitorError(
            "pyserial is required for the serial monitor.\n"
            "Install it with:  python -m pip install pyserial"
        )
    import serial.tools.list_ports

    return [p.device for p in serial.tools.list_ports.comports()]


def monitor_loop(ser, out=None, forward_input=True, stop=None, idle=0.02):
    """Read from ``ser`` and write to ``out`` until ``stop`` fires.

    ``ser`` only needs ``read(n) -> bytes`` and ``close()``. ``forward_input``
    relays console lines back to the board via ``ser.write``. ``stop`` is a
    threading.Event created and returned by :func:`stop_flag`.
    """
    out = out or sys.stdout
    t = None
    if forward_input:
        try:
            import threading
        except ImportError:
            threading = None
        if threading is not None and hasattr(sys.stdin, "fileno"):
            def feed():
                try:
                    for line in sys.stdin:
                        if stop is not None and stop.is_set():
                            return
                        try:
                            ser.write((line.rstrip("\r\n") + "\n").encode("utf-8"))
                        except Exception:
                            return
                except KeyboardInterrupt:
                    pass

            t = threading.Thread(target=feed, daemon=True)
            t.start()

    def emit(data):
        buf = out.buffer if hasattr(out, "buffer") else None
        if buf is not None:
            buf.write(data)
            buf.flush()
        else:
            out.write(data.decode("utf-8", "replace"))
            out.flush()

    try:
        while stop is None or not stop.is_set():
            try:
                data = ser.read(64)
            except EOFError:
                break
            except Exception:
                break
            if data:
                emit(data)
            else:
                time.sleep(idle)
    finally:
        if t is not None:
            t.join(timeout=1.0)
        try:
            ser.close()
        except Exception:
            pass


def stop_flag():
    """Return a threading.Event used to end :func:`monitor_loop`."""
    import threading

    return threading.Event()