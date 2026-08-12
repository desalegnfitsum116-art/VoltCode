"""Standalone tests for the Volt parser.

Run with:  python tests/test_parser.py
No external dependencies required.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from volt.lexer import Lexer  # noqa: E402
from volt.parser import Parser, ParseError  # noqa: E402
from volt.ast import (  # noqa: E402
    Program, ImportStmt, ModuleDecl, FuncDecl, Param, TypeRef,
    VarDecl, Assign, ExprStmt, ReturnStmt, IfStmt, IfBranch, WhileStmt,
    ForStmt, BreakStmt, ContinueStmt,
    IntLit, FloatLit, BoolLit, StrLit, NullLit, Ident, Unary, Binary,
    Call, Member, Index, ArrayLit,
)

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


def parse(src):
    return Parser(Lexer(src).tokenize()).parse_program()


def parse_err(src):
    try:
        Parser(Lexer(src).tokenize()).parse_program()
    except ParseError:
        return True
    return False


def only(program):
    assert len(program.statements) == 1, f"expected 1 stmt, got {len(program.statements)}"
    return program.statements[0]


# --------------------------------------------------------------------------- #


def test_literals():
    print("== literals ==")
    p = parse("var a = 42")
    vd = only(p)
    check("int literal", isinstance(vd, VarDecl) and isinstance(vd.value, IntLit) and vd.value.value == 42)

    p = parse("var b = 3.14")
    vd = only(p)
    check("float literal", isinstance(vd.value, FloatLit) and vd.value.value == 3.14)

    p = parse('var c = "hi"')
    vd = only(p)
    check("string literal", isinstance(vd.value, StrLit) and vd.value.value == "hi")

    p = parse("var d = true")
    vd = only(p)
    check("bool literal", isinstance(vd.value, BoolLit) and vd.value.value is True)

    p = parse("var e = null")
    vd = only(p)
    check("null literal", isinstance(vd.value, NullLit))

    p = parse("var f = [1, 2, 3]")
    vd = only(p)
    check(
        "array literal",
        isinstance(vd.value, ArrayLit)
        and len(vd.value.elements) == 3
        and all(isinstance(e, IntLit) for e in vd.value.elements),
    )


def test_var_decl_types():
    print("== variable declarations / types ==")
    p = parse("var x int = 0")
    vd = only(p)
    check(
        "no-colon annotation",
        isinstance(vd.type, TypeRef) and vd.type.base == "int" and not vd.type.array,
    )

    p = parse("var y: int = 0")
    vd = only(p)
    check("colon annotation", vd.type.base == "int")

    p = parse("var nums int[] = [1, 2]")
    vd = only(p)
    check("array type", vd.type.array and vd.type.base == "int" and vd.type.size is None)

    p = parse("var buf int[10]")
    vd = only(p)
    check("fixed-size array type", vd.type.array and vd.type.size == 10)

    p = parse("var s Servo = Servo.Init(5)")
    vd = only(p)
    check("module type annotation", isinstance(vd.type, TypeRef) and vd.type.base == "Servo")

    p = parse("var x = 5")
    vd = only(p)
    check("no annotation -> None", vd.type is None)


def test_hardware_init():
    print("== hardware init pattern ==")
    p = parse("var myServo = Servo.Init(5)")
    vd = only(p)
    call = vd.value
    check(
        "Init is a Call",
        isinstance(call, Call) and len(call.args) == 1,
    )
    callee = call.callee
    check(
        "callee is Member(Ident(Servo), Init)",
        isinstance(callee, Member)
        and isinstance(callee.obj, Ident)
        and callee.obj.name == "Servo"
        and callee.name == "Init",
    )
    check("arg is IntLit 5", isinstance(call.args[0], IntLit) and call.args[0].value == 5)

    p = parse("var b = Arduino.Init()")
    vd = only(p)
    call = vd.value
    check(
        "Arduino.Init() no args",
        isinstance(call, Call)
        and len(call.args) == 0
        and isinstance(call.callee, Member)
        and call.callee.name == "Init",
    )

    p = parse("myServo.write(90)")
    stmt = only(p)
    check(
        "follow-on method call",
        isinstance(stmt, ExprStmt)
        and isinstance(stmt.expr, Call)
        and isinstance(stmt.expr.callee, Member)
        and stmt.expr.callee.name == "write"
        and isinstance(stmt.expr.callee.obj, Ident)
        and stmt.expr.callee.obj.name == "myServo",
    )


def test_control_flow():
    print("== control flow ==")
    src = (
        "if x == 1:\n"
        "    var a = 1\n"
        "elif x == 2:\n"
        "    var b = 2\n"
        "else:\n"
        "    var c = 3\n"
    )
    p = parse(src)
    stmt = only(p)
    check("IfStmt", isinstance(stmt, IfStmt))
    check("two branches", len(stmt.branches) == 2)
    check("branch keywords", [b.keyword for b in stmt.branches] == ["if", "elif"])
    check("else body present", stmt.else_body is not None and len(stmt.else_body) == 1)

    src = "while x < 10:\n    x = x + 1\n"
    p = parse(src)
    stmt = only(p)
    check(
        "WhileStmt",
        isinstance(stmt, WhileStmt)
        and isinstance(stmt.condition, Binary)
        and stmt.condition.op == "<"
        and len(stmt.body) == 1,
    )

    src = "for i in [1, 2, 3]:\n    print(i)\n"
    p = parse(src)
    stmt = only(p)
    check(
        "ForStmt",
        isinstance(stmt, ForStmt)
        and stmt.var_name == "i"
        and isinstance(stmt.iterable, ArrayLit)
        and len(stmt.body) == 1,
    )

    src = "while x:\n    if y:\n        break\n    continue\n"
    p = parse(src)
    stmt = only(p)
    check(
        "break/continue inside blocks",
        isinstance(stmt.body[0], IfStmt)
        and isinstance(stmt.body[0].branches[0].body[0], BreakStmt)
        and isinstance(stmt.body[1], ContinueStmt),
    )


def test_functions():
    print("== functions ==")
    src = "func add(a int, b int) -> int:\n    return a + b\n"
    p = parse(src)
    fn = only(p)
    check("FuncDecl", isinstance(fn, FuncDecl) and fn.name == "add")
    check(
        "params with types",
        [param.name for param in fn.params] == ["a", "b"]
        and all(param.type.base == "int" for param in fn.params),
    )
    check("return type", fn.return_type is not None and fn.return_type.base == "int")
    check(
        "body return stmt",
        isinstance(fn.body[0], ReturnStmt)
        and isinstance(fn.body[0].value, Binary)
        and fn.body[0].value.op == "+",
    )

    src = "func noop():\n    return\n"
    p = parse(src)
    fn = only(p)
    check(
        "return without value",
        isinstance(fn.body[0], ReturnStmt) and fn.body[0].value is None,
    )

    src = "func f(s: Servo):\n    s.write(90)\n"
    p = parse(src)
    fn = only(p)
    check(
        "colon-style param annotation",
        fn.params[0].type.base == "Servo",
    )


def test_import_module():
    print("== import / module ==")
    p = parse("import Servo")
    stmt = only(p)
    check("ImportStmt", isinstance(stmt, ImportStmt) and stmt.name == "Servo")

    src = "module mymod:\n    var x = 1\n"
    p = parse(src)
    stmt = only(p)
    check(
        "ModuleDecl",
        isinstance(stmt, ModuleDecl)
        and stmt.name == "mymod"
        and len(stmt.body) == 1,
    )


def test_assignment():
    print("== assignment ==")
    p = parse("x = x + 1")
    stmt = only(p)
    check(
        "simple assign",
        isinstance(stmt, Assign)
        and isinstance(stmt.target, Ident)
        and isinstance(stmt.value, Binary),
    )

    p = parse("arr[0] = 5")
    stmt = only(p)
    check(
        "index assign",
        isinstance(stmt, Assign) and isinstance(stmt.target, Index),
    )

    p = parse("myServo.angle = 90")
    stmt = only(p)
    check(
        "member assign",
        isinstance(stmt, Assign) and isinstance(stmt.target, Member),
    )


def test_precedence():
    print("== operator precedence ==")
    p = parse("var r = 1 + 2 * 3")
    vd = only(p)
    expr = vd.value
    check(
        "multiplicative binds tighter",
        isinstance(expr, Binary)
        and expr.op == "+"
        and isinstance(expr.right, Binary)
        and expr.right.op == "*",
    )

    p = parse("var r = (1 + 2) * 3")
    vd = only(p)
    expr = vd.value
    check(
        "parentheses override precedence",
        isinstance(expr, Binary)
        and expr.op == "*"
        and isinstance(expr.left, Binary)
        and expr.left.op == "+",
    )

    p = parse("var r = not a and b")
    vd = only(p)
    expr = vd.value
    check(
        "and binds tighter than not-parse (not a and b)",
        isinstance(expr, Binary) and expr.op == "and",
    )

    p = parse("var r = a >= b and not c")
    vd = only(p)
    expr = vd.value
    check(
        "comparison before and; not binds tightest",
        isinstance(expr, Binary)
        and expr.op == "and"
        and isinstance(expr.left, Binary)
        and expr.left.op == ">="
        and isinstance(expr.right, Unary)
        and expr.right.op == "not",
    )

    p = parse("var r = -a + b")
    vd = only(p)
    expr = vd.value
    check(
        "unary minus",
        isinstance(expr, Binary)
        and expr.op == "+"
        and isinstance(expr.left, Unary)
        and expr.left.op == "-",
    )


def test_postfix_chains():
    print("== postfix chains ==")
    p = parse("var r = f(1, 2).value")
    vd = only(p)
    check(
        "call then member",
        isinstance(vd.value, Member) and isinstance(vd.value.obj, Call),
    )

    p = parse("var r = a.b.c")
    vd = only(p)
    check(
        "chained members",
        isinstance(vd.value, Member)
        and isinstance(vd.value.obj, Member)
        and vd.value.obj.name == "b",
    )

    p = parse("var r = matrix[0][1]")
    vd = only(p)
    check(
        "chained indexes",
        isinstance(vd.value, Index) and isinstance(vd.value.obj, Index),
    )

    p = parse("var r = a[0].size")
    vd = only(p)
    check(
        "index then member",
        isinstance(vd.value, Member) and isinstance(vd.value.obj, Index),
    )


def test_semicolons():
    print("== semicolons ==")
    p = parse("var a = 1; var b = 2\nvar c = 3")
    check(
        "multiple statements parsed",
        len(p.statements) == 3
        and all(isinstance(s, VarDecl) for s in p.statements),
    )


def test_errors():
    print("== parse errors ==")
    check("missing colon after if", parse_err("if x\n    var a = 1"))
    check("missing indent", parse_err("if x:\nvar a = 1"))
    check(
        "block auto-closed at EOF (no trailing newline)",
        isinstance(only(parse("if x:\n    var a = 1")), IfStmt),
    )
    check("missing expression", parse_err("var x = "))
    check("missing paren", parse_err("var x = (1 + 2"))
    check("missing rparen in call", parse_err("var x = f(1"))
    check("missing comma-term expr", parse_err("var x = 1 +"))
    check("dangling dot", parse_err("var x = obj."))
    check("bad member after dot", parse_err("var x = obj.123"))
    check("empty type", parse_err("var x : = 1"))
    check("missing module name", parse_err("module 123:"))


def test_positions():
    print("== position tracking ==")
    p = parse("var a = 1\nvar bb = 22")
    second = p.statements[1]
    check("line", second.line == 2)
    check("col", second.col == 1)

    p = parse("var x = myServo.write(90)")
    vd = only(p)
    call = vd.value
    check("call line", call.line == 1)


def test_empty_program():
    print("== empty program ==")
    p = parse("")
    check("empty program", isinstance(p, Program) and len(p.statements) == 0)

    p = parse("// just a comment\n")
    check("comment-only program", isinstance(p, Program) and len(p.statements) == 0)


def main():
    test_literals()
    test_var_decl_types()
    test_hardware_init()
    test_control_flow()
    test_functions()
    test_import_module()
    test_assignment()
    test_precedence()
    test_postfix_chains()
    test_semicolons()
    test_errors()
    test_positions()
    test_empty_program()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
