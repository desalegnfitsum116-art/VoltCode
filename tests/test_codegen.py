"""Standalone tests for the Volt -> C code generator.

Run with:  python tests/test_codegen.py
No external dependencies required.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from volt.lexer import Lexer  # noqa: E402
from volt.parser import Parser  # noqa: E402
from volt.sema import Analyzer  # noqa: E402
from volt.codegen import CCodeGen, CodegenError  # noqa: E402

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


def gen(src):
    """Return the generated C for source that must analyze cleanly."""
    tree = Parser(Lexer(src).tokenize()).parse_program()
    analyzer = Analyzer("<test>")
    ok = analyzer.analyze(tree)
    if not ok:
        raise AssertionError("analysis failed: " + "; ".join(str(d) for d in analyzer.diagnostics))
    return CCodeGen("<test>").generate(tree)


def gen_error(src):
    """Return the message of the first CodegenError raised for source."""
    tree = Parser(Lexer(src).tokenize()).parse_program()
    analyzer = Analyzer("<test>")
    analyzer.analyze(tree)
    try:
        CCodeGen("<test>").generate(tree)
    except CodegenError as exc:
        return exc.message
    return None


def expect_contains(name, src, substring):
    check(name, substring in gen(src))


def test_blink():
    print("== blink program ==")
    out = gen(
        "var led = DigitalPin.Init(13, DigitalPin.OUTPUT)\n"
        "var board = Arduino.Init()\n"
        "while true:\n"
        "    led.write(1)\n"
        "    Delay(1000)\n"
        "    led.write(0)\n"
        "    Delay(1000)\n"
    )
    check("Arduino include", "#include <Arduino.h>" in out)
    check("no Servo include", "#include <Servo.h>" not in out)
    check("DigitalPin struct", "VoltDigitalPin" in out)
    check("handle globals", "VoltDigitalPin* led;" in out and "VoltArduino* board;" in out)
    check("setup contains inits", (
        "led = volt_digitalpin_init(&__volt_digitalpin_0, 13, OUTPUT);" in out
        and "board = volt_arduino_init(&__volt_arduino_1);" in out
    ))
    check("setup before loop", out.index("void setup()") < out.index("void loop()"))
    check("loop holds while", "void loop()" in out and "while (true)" in out)
    check("method call translated", "digitalWrite(led->pin, 1);" in out)
    check("Delay maps to delay", "delay(1000);" in out)


def test_servo_sweep():
    print("== servo sweep ==")
    out = gen(
        "import Servo\n"
        "var board = Arduino.Init()\n"
        "var myServo = Servo.Init(9)\n"
        "var pos int = 0\n"
        "func sweep(start int, end int, step int):\n"
        "    var angle = start\n"
        "    while angle <= end:\n"
        "        myServo.write(angle)\n"
        "        Delay(15)\n"
        "        angle = angle + step\n"
        "while true:\n"
        "    sweep(0, 180, 1)\n"
        "    sweep(180, 0, -1)\n"
    )
    check("Servo include", "#include <Servo.h>" in out)
    check("servo helper", "volt_servo_init(Servo* self, int16_t pin)" in out)
    check("servo storage call", "myServo = volt_servo_init(&__volt_servo_1, 9);" in out)
    check("servo method", "myServo->write(angle);" in out)
    check("function prototype", "void sweep(int16_t start, int16_t end, int16_t step);" in out)
    check("function definition", "void sweep(int16_t start, int16_t end, int16_t step) {" in out)
    check("constant global inline", "int16_t pos = 0;" in out)
    check("arithmetic", "angle = (angle + step);" in out)
    check("negative arg", "sweep(180, 0, (-1));" in out)
    check("functions not in loop body", "sweep(0, 180, 1);" in out)


def test_type_mapping():
    print("== type mapping ==")
    out = gen("var i int = 5\nvar f float = 1.5\nvar b bool = true\nvar s string = \"hi\"\n")
    check("int16_t", "int16_t i = 5;" in out)
    check("float suffix", "float f = 1.5f;" in out)
    check("bool", "bool b = true;" in out)
    check("string pointer", 'const char* s = "hi";' in out)


def test_handles():
    print("== handle params / returns / null ==")
    out = gen(
        "var s Servo = null\n"
        "func drive(s Servo) -> int:\n"
        "    s.write(90)\n"
        "    return s.read()\n"
        "var r = drive(s)\n"
    )
    check("null global", "Servo* s = NULL;" in out)
    check("handle param type", "int16_t drive(Servo* s)" in out)
    check("method on param", "s->write(90);" in out)
    check("method return", "return s->read();" in out)
    check("runtime assign in setup", "r = drive(s);" in out)
    check("empty loop present", "void loop() {" in out)


def test_arrays():
    print("== arrays ==")
    out = gen(
        "var a int[3] = [1, 2, 3]\n"
        "var b int[5] = [1, 2]\n"
        "var c int[2] = []\n"
        "for x in a:\n"
        "    b[x] = x + 1\n"
    )
    check("brace init full", "int16_t a[3] = {1, 2, 3};" in out)
    check("brace init partial", "int16_t b[5] = {1, 2};" in out)
    check("empty brace init", "int16_t c[2] = {};" in out)
    check("for counter loop", "for (size_t __volt_for_0 = 0; __volt_for_0 < 3; ++__volt_for_0)" in out)
    check("loop var holds element", "int16_t x = a[__volt_for_0];" in out)
    check("element assign", "b[x] = (x + 1);" in out)


def test_expressions():
    print("== expressions ==")
    out = gen('var s = "abc"\nif s == "abc":\n    var x = 1\n')
    check("string compare", 'strcmp(s, "abc") == 0' in out)
    check("if condition", 'if ((strcmp(s, "abc") == 0)) {' in out)

    out = gen("var a = 1\nvar b = true\nif a and b:\n    var x = 1\nwhile a or b:\n    break\n")
    check("and translates", "if (((a) && (b))) {" in out)
    check("or translates", "while (((a) || (b))) {" in out)

    out = gen("var h = Servo.Init(9)\nif h:\n    h.detach()\n")
    check("pointer truthiness", "if (h) {" in out)
    check("servo detach", "h->detach();" in out)


def test_builtins_and_runtime():
    print("== builtins / runtime layout ==")
    out = gen("var t = Millis()\nDelayMicroseconds(10)\n")
    check("Millis maps to millis", "t = millis();" in out)
    check("DelayMicroseconds maps", "delayMicroseconds(10);" in out)
    check("time calls in setup", out.index("t = millis();") > out.index("void setup()"))


def test_minimal_output():
    print("== minimal program ==")
    out = gen("var x = 5\n")
    check("no structs when unused", "typedef struct" not in out)
    check("no Servo include", "#include <Servo.h>" not in out)
    check("global constant", "int16_t x = 5;" in out)
    check("entry points always present", "void setup() {" in out and "void loop() {" in out)


def test_codegen_errors():
    print("== codegen errors ==")
    msg = gen_error("func f(a int[]):\n    for x in a:\n        var y = x\n")
    check("for over unsized array", msg is not None and "without a known size" in msg)
    msg = gen_error("func f(a int[]):\n    var x int[3] = a\n")
    check("array non-literal init", msg is not None and "array literal" in msg)


def main():
    test_blink()
    test_servo_sweep()
    test_type_mapping()
    test_handles()
    test_arrays()
    test_expressions()
    test_builtins_and_runtime()
    test_minimal_output()
    test_codegen_errors()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
