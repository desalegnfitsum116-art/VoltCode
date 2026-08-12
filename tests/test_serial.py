"""Standalone tests for Stage 6: Serial I/O (language + host monitor).

Includes the `Serial` module translation (sema + codegen) and the host-side
`monitor_loop` with a fake serial device, so no real hardware/pyserial is
required. pyserial is only needed for actual monitor use.

Run with:  python tests/test_serial.py
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from volt.lexer import Lexer  # noqa: E402
from volt.parser import Parser  # noqa: E402
from volt.sema import Analyzer  # noqa: E402
from volt.codegen import CCodeGen  # noqa: E402
from volt import monitor as mon  # noqa: E402

PASS = 0
FAIL = 0


def check(name, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def analyze(src):
    tree = Parser(Lexer(src).tokenize()).parse_program()
    analyzer = Analyzer("<test>")
    ok = analyzer.analyze(tree)
    return analyzer, tree, ok


def gen(src):
    analyzer, tree, ok = analyze(src)
    if not ok:
        raise AssertionError("analysis failed: " + "; ".join(str(d) for d in analyzer.diagnostics))
    return CCodeGen("<test>").generate(tree)


def sema_error(src):
    analyzer, _tree, _ok = analyze(src)
    return " ".join(str(d) for d in analyzer.diagnostics if d.is_error)


SRC = (
    "var board = Arduino.Init()\n"
    "Serial.begin(9600)\n"
    "Serial.println(\"ready\")\n"
    "var ch int = 0\n"
    "while true:\n"
    "    if Serial.available() > 0:\n"
    "        ch = Serial.read()\n"
    "        Serial.write(ch)\n"
)


def test_codegen():
    print("== Serial codegen ==")
    out = gen(SRC)
    check("begin in setup", "Serial.begin(9600);" in out)
    check("println in setup", 'Serial.println("ready");' in out)
    check("available mapped", "Serial.available()" in out)
    check("read mapped", "ch = Serial.read();" in out)
    check("write mapped", "Serial.write(ch);" in out)
    check("no serial storage", "__volt_serial_" not in out)


def test_print_types():
    print("== Serial.print/println accepted types ==")
    for lit in ['"hi"', "42", "3.5", "true"]:
        out = gen(f"Serial.begin(9600)\nSerial.print({lit})\n")
        check(f"print({lit})", (f"Serial.print({lit});" if lit != "3.5" else "Serial.print(3.5f);") in out)
    out = gen(f"Serial.begin(9600)\nSerial.println(7)\n")
    check("println(int)", "Serial.println(7);" in out)


def test_sema_errors():
    print("== Serial sema errors ==")
    msg = sema_error("Serial.begin(9600)\nSerial.print()\n")
    check("print arity", "expects 1 argument" in msg)
    msg = sema_error("Serial.begin(9600)\nSerial.Init()\n")
    check("no Init on Serial", "Serial has no method 'Init'" in msg)
    msg = sema_error("Serial.begin(9600)\nSerial.available(3)\n")
    check("wrong method arity", "expects 0 argument" in msg)


def test_monitor():
    print("== monitor loop ==")

    class FakeSerial:
        def __init__(self, chunks):
            self._chunks = list(chunks)
            self.closed = False

        def read(self, n):
            if not self._chunks:
                raise EOFError
            return self._chunks.pop(0)

        def write(self, data):
            self.written = self.written + data if hasattr(self, "written") else data

        def close(self):
            self.closed = True

    out = io.StringIO()
    fake = FakeSerial([b"hello\r\n", b"from ", b"board\r\n"])
    fake._chunks.clear()  # let read raise EOFError immediately
    fake._chunks.append(b"hello\r\n")
    fake._chunks.append(b"from board\r\n")
    mon.monitor_loop(fake, out=out, forward_input=False, idle=0)
    check("prints incoming bytes", out.getvalue() == "hello\r\nfrom board\r\n")
    check("closes the port", fake.closed)

    # EOF stops cleanly even with nothing printed.
    empty = FakeSerial([])
    empty.closed = False
    mon.monitor_loop(empty, out=io.StringIO(), forward_input=False, idle=0)
    check("EOF stops cleanly", empty.closed)


def test_open_error():
    print("== open_serial error path ==")
    try:
        mon.open_serial("DOES-NOT-EXIST-12345", 9600)
        check("bad port raises", False)
    except mon.MonitorError as exc:
        check("bad port raises", "cannot open" in str(exc))


def main():
    test_codegen()
    test_print_types()
    test_sema_errors()
    test_monitor()
    test_open_error()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())