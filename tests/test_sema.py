"""Standalone tests for the Volt semantic analyzer.

Run with:  python tests/test_sema.py
No external dependencies required.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from volt.lexer import Lexer  # noqa: E402
from volt.parser import Parser  # noqa: E402
from volt.sema import Analyzer  # noqa: E402
from volt.types import INT, FLOAT, BOOL, STRING, ArrayType, ModuleType  # noqa: E402

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
    """Return (ok, diagnostics) for a source string."""
    tree = Parser(Lexer(src).tokenize()).parse_program()
    analyzer = Analyzer("<test>")
    ok = analyzer.analyze(tree)
    return ok, analyzer.diagnostics


def errors(src):
    ok, diags = analyze(src)
    return [d for d in diags if d.is_error]


def warnings(src):
    ok, diags = analyze(src)
    return [d for d in diags if not d.is_error]


def error_msgs(src):
    return [d.message for d in errors(src)]


def warning_msgs(src):
    return [d.message for d in warnings(src)]


def expect_no_errors(name, src):
    errs = errors(src)
    check(name, len(errs) == 0)


def expect_error(name, src, substring):
    msgs = error_msgs(src)
    check(name, any(substring in m for m in msgs))


# --------------------------------------------------------------------------- #


def test_valid_programs():
    print("== valid programs ==")
    expect_no_errors("blink program", (
        "var led = DigitalPin.Init(13, DigitalPin.OUTPUT)\n"
        "var board = Arduino.Init()\n"
        "while true:\n"
        "    led.write(1)\n"
        "    Delay(1000)\n"
        "    led.write(0)\n"
        "    Delay(1000)\n"
    ))
    expect_no_errors("servo sweep", (
        "import Servo\n"
        "var board = Arduino.Init()\n"
        "var myServo = Servo.Init(9)\n"
        "func sweep(start int, end int, step int) -> int:\n"
        "    var angle = start\n"
        "    while angle <= end:\n"
        "        angle = angle + step\n"
        "    return angle\n"
        "var done = sweep(0, 180, 1)\n"
        "myServo.write(done)\n"
    ))
    expect_no_errors("inference + int->float widening", "var x float = 5\nvar y = x + 1.5\n")
    expect_no_errors("null handle", "var s Servo = null\n")
    expect_no_errors("for loop", "var arr int[] = [1, 2, 3]\nfor i in arr:\n    arr[i] = 0\n")
    expect_no_errors("handle param passing", (
        "func drive(s Servo) -> int:\n"
        "    s.write(90)\n"
        "    return s.read()\n"
        "var s = Servo.Init(9)\n"
        "var r = drive(s)\n"
    ))
    expect_no_errors("empty array with size annotation", "var buf int[10] = []\n")
    expect_no_errors("literal smaller than declared size", "var buf int[10] = [1, 2, 3]\n")
    expect_no_errors("bool ops", "var a = true\nvar b = false\nvar c = a and not b or b\n")


def test_scope():
    print("== scope / resolution ==")
    expect_error("undefined variable", "var x = missing\n", "undefined identifier 'missing'")
    expect_error("undefined function", "f()\n", "undefined function 'f'")
    expect_error("use before nested scope", (
        "if true:\n"
        "    var inner = 1\n"
        "var x = inner\n"
    ), "undefined identifier 'inner'")
    expect_no_errors("block scope var visible inside", (
        "if true:\n"
        "    var inner = 1\n"
        "    var x = inner\n"
    ))
    expect_no_errors("global used in function", (
        "var g = 5\n"
        "func f() -> int:\n"
        "    return g\n"
    ))
    expect_error("duplicate variable", "var a = 1\nvar a = 2\n", "already defined")
    expect_error("duplicate param", "func f(a int, a int):\n    var x = 1\n", "already defined")
    expect_error("duplicate function", "func f():\n    var x = 1\nfunc f():\n    var y = 2\n", "already defined")
    expect_error("reserved function name", "func setup():\n    var x = 1\n", "reserved name")
    expect_error("reserved global var name", "var loop = 5\n", "reserved name")
    expect_no_errors("reserved name ok as local var", "func f() -> int:\n    var setup = 5\n    return setup\n")
    check("shadowing warning", "shadows an outer" in " ".join(warning_msgs(
        "var a = 1\nfunc f():\n    var a = 2\n"
    )))
    check("unused var warning", "unused variable 'u'" in " ".join(warning_msgs(
        "func f():\n    var u = 1\n    var used = 2\n    return used\n"
    )))
    check("unused param warning", "unused variable 'p'" in " ".join(warning_msgs(
        "func f(p int) -> int:\n    return 1\n"
    )))


def test_types():
    print("== type checking ==")
    expect_error("int to string", 'var x int = "hi"\n', "cannot assign string to variable 'x'")
    expect_error("string to int", 'var x = 5\nx = "hi"\n', "cannot assign string to 'x'")
    expect_error("no narrowing float->int", "var x int = 5.0\n", "cannot assign float")
    expect_no_errors("int->float widening", "var x float = 5\n")
    expect_error("bad arithmetic", 'var s = "a"\nvar x = s + 1\n', "requires numeric operands")
    expect_error("modulo floats", "var x = 5.0 % 2\n", "requires int operands")
    expect_error("compare int and string", 'var x = 1 == "a"\n', "cannot compare int and string")
    expect_no_errors("compare handle to null", "var s Servo = null\nif s == null:\n    var x = 1\n")
    expect_error("and on wrong types", 'var x = 1 and "a"\n', "must be a bool, int, float, or handle")
    expect_error("array index on int", "var a = 5\nvar x = a[0]\n", "cannot index")
    expect_error("index must be int", 'var a int[] = [1]\nvar x = a["0"]\n', "index must be an int")
    expect_error("mixed array elements", "var a = [1, \"two\"]\n", "same type")
    expect_error("empty array literal", "var a = []\n", "empty array literal")
    expect_no_errors("array element assign", "var a int[] = [1, 2]\na[0] = 5\n")
    expect_error("array element type mismatch", "var a int[] = [1, 2]\na[0] = 5.0\n", "array element")
    expect_error("unknown type", "var x Banana = 1\n", "unknown type 'Banana'")
    expect_error("var without type or init", "var x\n", "requires a type annotation or an initializer")
    expect_error("void in var", "var x = Delay(5)\n", "void value")
    expect_error("null inference", "var x = null\n", "cannot infer")
    expect_error("empty array without size", "var buf int[] = []\n", "cannot determine the size")
    expect_error("array var without size or init", "var buf int[]\n", "needs a fixed size or an initializer")
    expect_error("assign to whole array", "var a int[3] = [1, 2, 3]\nvar b int[3] = a\nb = a\n", "cannot assign to the whole array")


def test_functions():
    print("== functions ==")
    expect_error("wrong arg count", "func f(a int):\n    var x = 1\nf()\n", "expects 1 argument(s)")
    expect_error("wrong arg type", "func f(a int):\n    var x = 1\nf(\"s\")\n", "expected int")
    expect_error("bad return type", "func f() -> int:\n    return \"s\"\n", "cannot return string")
    expect_error("return value in void fn", "func f():\n    return 1\n", "does not return a value")
    expect_error("return in non-void without value", "func f() -> int:\n    return\n", "has no value")
    expect_error("return at top level", "return 1\n", "not allowed at the top level")
    expect_no_errors("void fn with bare return", "func f():\n    return\n")
    check("no-return warning", "never returns a value" in " ".join(warning_msgs(
        "func f() -> int:\n    var x = 1\n"
    )))
    expect_no_errors("call with int->float widening", "func f(a float):\n    var x = 1\nf(2)\n")
    expect_error("function used as value", "func f():\n    var x = 1\nvar y = f\n", "cannot be used as a value")


def test_control_flow():
    print("== control flow ==")
    expect_error("break outside loop", "break\n", "'break' is only allowed inside a loop")
    expect_error("continue outside loop", "continue\n", "'continue' is only allowed inside a loop")
    expect_no_errors("break inside nested loop", "while true:\n    while true:\n        break\n    continue\n")
    expect_error("if condition not truthy", "if \"s\":\n    var x = 1\n", "must be a bool, int, float, or handle")
    expect_error("while condition not truthy", "while \"s\":\n    var x = 1\n", "must be a bool, int, float, or handle")
    expect_error("for over non-array", "var x = 5\nfor i in x:\n    var y = 1\n", "cannot iterate over")


def test_hardware_validation():
    print("== hardware validation ==")
    expect_error("unknown module import", "import Motor\n", "unknown module 'Motor'")
    expect_error("unknown module member", "var s = Servo.Init(5)\ns.unknown(1)\n", "no method 'unknown'")
    expect_error("bad module method name", "var s = Servo.Init(5)\ns.writ(90)\n", "no method 'writ'")
    expect_error("init on wrong module method", "var b = Arduino.Init()\nb.write(90)\n", "no method 'write'")
    expect_error("servo pin too high", "var s = Servo.Init(14)\n", "pin 14 is out of range")
    expect_error("servo pin negative", "var s = Servo.Init(-1)\n", "pin -1 is out of range")
    expect_no_errors("servo pin boundary", "var s = Servo.Init(13)\n")
    expect_error("analog pin out of range", "var a = AnalogPin.Init(6)\n", "pin 6 is out of range")
    expect_error("bad digital mode literal", "var d = DigitalPin.Init(13, 7)\n", "invalid mode 7")
    expect_no_errors("digital mode constant", "var d = DigitalPin.Init(13, DigitalPin.OUTPUT)\n")
    expect_no_errors("digital mode as var", "var m = DigitalPin.INPUT\nvar d = DigitalPin.Init(13, m)\n")
    expect_error("servo init wrong arg count", "var s = Servo.Init()\n", "expects 1 argument(s)")
    expect_error("servo init wrong arg type", "var s = Servo.Init(\"x\")\n", "expected int")
    expect_error("method used as value", "var s = Servo.Init(5)\nvar f = s.write\n", "cannot be used as a value")
    expect_error("assign to method", "var s = Servo.Init(5)\ns.write = 90\n", "cannot assign to method")
    expect_error("assign to constant", "DigitalPin.OUTPUT = 2\n", "cannot assign to constant")
    expect_error("call module directly", "Servo(5)\n", "module 'Servo' cannot be called")
    expect_error("call constant", "DigitalPin.OUTPUT()\n", "cannot call constant")
    expect_no_errors("module constant as value", "var m = DigitalPin.OUTPUT\n")


def test_error_recovery():
    print("== error recovery ==")
    ok, diags = analyze("var a = missing1\nvar b = missing2\n")
    check("reports multiple errors", len(errors("var a = missing1\nvar b = missing2\n")) == 2)
    check("ok flag reflects errors", ok is False and len([d for d in diags if d.is_error]) == 2)


def test_inferred_types():
    print("== inferred type annotation ==")
    from volt.ast import VarDecl, Binary
    tree = Parser(Lexer("var x = 5\nvar y = 2.5\nvar z = [1, 2]\n").tokenize()).parse_program()
    analyzer = Analyzer("<test>")
    analyzer.analyze(tree)
    x = tree.statements[0]
    y = tree.statements[1]
    z = tree.statements[2]
    check("int inferred", isinstance(x, VarDecl) and x.inferred_type is INT)
    check("float inferred", isinstance(y, VarDecl) and y.inferred_type is FLOAT)
    check("array inferred", isinstance(z, VarDecl) and isinstance(z.inferred_type, ArrayType) and z.inferred_type.element is INT)
    check("array size filled from literal", isinstance(z, VarDecl) and isinstance(z.inferred_type, ArrayType) and z.inferred_type.size == 2)

    tree = Parser(Lexer("var s = Servo.Init(5)\n").tokenize()).parse_program()
    analyzer = Analyzer("<test>")
    analyzer.analyze(tree)
    vd = tree.statements[0]
    check("handle type inferred", isinstance(vd.inferred_type, ModuleType) and vd.inferred_type.name == "Servo")


def main():
    test_valid_programs()
    test_scope()
    test_types()
    test_functions()
    test_control_flow()
    test_hardware_validation()
    test_error_recovery()
    test_inferred_types()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
