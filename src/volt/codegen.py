"""Stage 4: Volt -> C code generation.

Produces Arduino C++ (an Arduino sketch compiles as C++), because the Servo
library is a C++ class. The generator expects the AST to have already passed
semantic analysis (every node carries ``inferred_type``).

Design (see docs/volt-language-spec.md):

* A top-level ``var`` with a constant initializer becomes a C global with an
  inline initializer; otherwise the global is declared empty and assigned in
  ``setup()`` in source order.
* Every ``Module.Init(...)`` call site gets unique static storage
  (``__volt_<module>_<n>``). A handle value is a pointer to that storage, so
  ``var s = Servo.Init(9)`` is ``Servo* s;`` + ``s = volt_servo_init(&__volt_servo_0, 9);``.
* ``setup()`` holds runtime top-level initializers and bare top-level Init
  statements; ``loop()`` holds every other top-level statement.
* User functions become C functions with prototypes; array parameters decay to
  element pointers; ``for`` loops use an explicit counter with the array size.
"""

from __future__ import annotations

from .ast import (
    Program,
    ImportStmt,
    ModuleDecl,
    FuncDecl,
    VarDecl,
    Assign,
    ExprStmt,
    ReturnStmt,
    IfStmt,
    WhileStmt,
    ForStmt,
    BreakStmt,
    ContinueStmt,
    IntLit,
    FloatLit,
    BoolLit,
    StrLit,
    NullLit,
    Ident,
    Unary,
    Binary,
    Call,
    Member,
    Index,
    ArrayLit,
    ast_children,
)
from .types import (
    INT,
    FLOAT,
    BOOL,
    STRING,
    VOID,
    NULL,
    ArrayType,
    ModuleType,
)

# Volt int maps to AVR's native 16-bit int.
HANDLE_C_TYPES = {
    "Servo": "Servo*",
    "DigitalPin": "VoltDigitalPin*",
    "AnalogPin": "VoltAnalogPin*",
    "Arduino": "VoltArduino*",
}

# Module.Init(...) call sites dispatch through a uniform helper function.
INIT_HELPERS = {
    "Servo": "volt_servo_init",
    "DigitalPin": "volt_digitalpin_init",
    "AnalogPin": "volt_analogpin_init",
    "Arduino": "volt_arduino_init",
}

# Volt builtin functions -> Arduino runtime calls.
BUILTIN_C = {
    "Delay": "delay",
    "DelayMicroseconds": "delayMicroseconds",
    "Millis": "millis",
    "Micros": "micros",
}

# Singleton modules used as a static namespace on the module name itself
# (no Init/handle/storage needed). Calls translate to the Arduino object of
# the same name, e.g. Serial.begin(9600) -> Serial.begin(9600).
STATIC_MODULES = ("Serial",)


class CodegenError(Exception):
    """Raised when the validated AST cannot be lowered to C."""

    def __init__(self, message, line=0, col=0, filename="<string>"):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col
        self.filename = filename

    def __str__(self):
        loc = f"{self.filename}:{self.line}:{self.col}" if self.line else self.filename
        return f"{loc}: codegen error: {self.message}"


class CCodeGen:
    def __init__(self, filename="<string>"):
        self.filename = filename
        self.out = []
        self.indent = 0
        self.used_modules = set()       # module names referenced anywhere
        self.init_modules = set()       # modules that have an Init call site
        self.storage_index = 0
        self.storage_decls = []         # (storage_name, module_name) for Init sites
        self.for_counter = 0

    # ------------------------------------------------------------------ #
    # Writer helpers                                                      #
    # ------------------------------------------------------------------ #

    def line(self, text=""):
        self.out.append("    " * self.indent + text)

    def block(self, header):
        self.line(header + " {")
        self.indent += 1

    def end_block(self):
        self.indent -= 1
        self.line("}")

    def err(self, message, node=None):
        line = getattr(node, "line", 0) if node is not None else 0
        col = getattr(node, "col", 0) if node is not None else 0
        raise CodegenError(message, line, col, self.filename)

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def generate(self, program: Program) -> str:
        self.collect_inits(program)
        setup_stmts, loop_stmts = self._split_top_level(program.statements)

        self._emit_header()
        self._emit_typedefs()
        self._emit_helpers()
        self._emit_globals(program.statements)
        self._emit_prototypes(program.statements)
        self._emit_functions(program.statements)
        self._emit_setup(setup_stmts)
        self._emit_loop(loop_stmts)
        return "\n".join(self.out)

    # ------------------------------------------------------------------ #
    # Pre-scan: find Init call sites and every referenced module           #
    # ------------------------------------------------------------------ #

    def _walk(self, node):
        yield node
        for child in ast_children(node):
            yield from self._walk(child)

    def collect_inits(self, program):
        for node in self._walk(program):
            ty = getattr(node, "inferred_type", None)
            if isinstance(ty, ModuleType):
                self.used_modules.add(ty.name)
            if isinstance(node, FuncDecl):
                for pt in getattr(node, "resolved_params", []):
                    if isinstance(pt, ModuleType):
                        self.used_modules.add(pt.name)
                rt = getattr(node, "resolved_return", None)
                if isinstance(rt, ModuleType):
                    self.used_modules.add(rt.name)
            if (
                isinstance(node, Call)
                and isinstance(node.callee, Member)
                and node.callee.name == "Init"
                and isinstance(ty, ModuleType)
            ):
                name = f"__volt_{ty.name.lower()}_{self.storage_index}"
                self.storage_index += 1
                node._volt_storage = name
                self.storage_decls.append((name, ty.name))
                self.used_modules.add(ty.name)
                self.init_modules.add(ty.name)

    # ------------------------------------------------------------------ #
    # Top-level statement split                                           #
    # ------------------------------------------------------------------ #

    def _split_top_level(self, statements):
        setup_stmts = []
        loop_stmts = []
        for stmt in statements:
            if isinstance(stmt, (ImportStmt, ModuleDecl, FuncDecl)):
                continue
            if isinstance(stmt, VarDecl):
                setup_stmts.append(stmt)
            elif (
                isinstance(stmt, ExprStmt)
                and self._is_setup_expression(stmt.expr)
            ):
                setup_stmts.append(stmt)
            else:
                loop_stmts.append(stmt)
        return setup_stmts, loop_stmts

    def _is_setup_expression(self, expr):
        """A bare expression statement belongs in setup() when it is an Init()
        call (has storage) or a call on a static singleton module (Serial)."""
        if not isinstance(expr, Call):
            return False
        callee = expr.callee
        if not isinstance(callee, Member) or not isinstance(callee.obj, Ident):
            return False
        if callee.obj.name in STATIC_MODULES:
            return True
        return getattr(expr, "_volt_storage", None) is not None

    # ------------------------------------------------------------------ #
    # Header / types / helpers                                            #
    # ------------------------------------------------------------------ #

    def _emit_header(self):
        self.line("// Generated by voltc. Do not edit.")
        self.line()
        self.line("#include <Arduino.h>")
        if "Servo" in self.used_modules:
            self.line("#include <Servo.h>")
        self.line("#include <stdint.h>")
        self.line("#include <stdbool.h>")
        self.line("#include <string.h>")
        self.line()

    def _emit_typedefs(self):
        for name in ("DigitalPin", "AnalogPin", "Arduino"):
            if name not in self.used_modules:
                continue
            self.block("typedef struct")
            if name == "DigitalPin":
                self.line("uint8_t pin;")
                self.line("uint8_t mode;")
            elif name == "AnalogPin":
                self.line("uint8_t pin;")
            else:  # Arduino
                self.line("uint8_t _unused;")
            self.indent -= 1
            self.line(f"}} Volt{name};")
        if "DigitalPin" in self.used_modules or "AnalogPin" in self.used_modules or "Arduino" in self.used_modules:
            self.line()

    def _emit_helpers(self):
        helpers = []
        if "Servo" in self.init_modules:
            helpers.append((
                "static Servo* volt_servo_init(Servo* self, int16_t pin)",
                ["self->attach(pin);"],
            ))
        if "DigitalPin" in self.init_modules:
            helpers.append((
                "static VoltDigitalPin* volt_digitalpin_init(VoltDigitalPin* self, int16_t pin, int16_t mode)",
                [
                    "self->pin = (uint8_t)pin;",
                    "self->mode = (uint8_t)mode;",
                    "pinMode(pin, mode);",
                ],
            ))
        if "AnalogPin" in self.init_modules:
            helpers.append((
                "static VoltAnalogPin* volt_analogpin_init(VoltAnalogPin* self, int16_t pin)",
                ["self->pin = (uint8_t)pin;"],
            ))
        if "Arduino" in self.init_modules:
            helpers.append((
                "static VoltArduino* volt_arduino_init(VoltArduino* self)",
                ["(void)self;"],
            ))
        if not helpers:
            return
        for header, body in helpers:
            self.block(header)
            for b in body:
                self.line(b)
            self.line("return self;")
            self.end_block()
            self.line()

    # ------------------------------------------------------------------ #
    # Globals                                                             #
    # ------------------------------------------------------------------ #

    def _emit_globals(self, statements):
        for stmt in statements:
            if not isinstance(stmt, VarDecl):
                continue
            ty = stmt.inferred_type
            value = stmt.value
            if isinstance(ty, ArrayType):
                if ty.size is None:
                    self.err("array variable has no fixed size", stmt)
                if value is not None and not isinstance(value, ArrayLit):
                    self.err("arrays can only be initialized with an array literal", stmt)
                decl = f"{self.c_type(ty.element)} {stmt.name}[{ty.size}]"
                if value is not None and self.is_constant(value):
                    elems = ", ".join(self.emit_expr(e) for e in value.elements)
                    self.line(f"{decl} = {{{elems}}};")
                else:
                    self.line(f"{decl};")
            else:
                if value is not None and self.is_constant(value):
                    self.line(f"{self.c_type(ty)} {stmt.name} = {self.emit_expr(value)};")
                else:
                    self.line(f"{self.c_type(ty)} {stmt.name};")
        if self.storage_decls:
            for name, mod in self.storage_decls:
                pointee = HANDLE_C_TYPES[mod]
                if pointee.endswith("*"):
                    pointee = pointee[:-1].rstrip()
                self.line(f"static {pointee} {name};")
            self.line()
        if any(isinstance(s, VarDecl) for s in statements):
            self.line()

    # ------------------------------------------------------------------ #
    # Functions                                                           #
    # ------------------------------------------------------------------ #

    def _emit_prototypes(self, statements):
        for stmt in statements:
            if not isinstance(stmt, FuncDecl):
                continue
            params = ", ".join(
                f"{self.c_param_type(pt)} {p.name}"
                for p, pt in zip(stmt.params, stmt.resolved_params)
            )
            self.line(f"{self.c_type(stmt.resolved_return)} {stmt.name}({params});")
        if any(isinstance(s, FuncDecl) for s in statements):
            self.line()

    def _emit_functions(self, statements):
        for stmt in statements:
            if not isinstance(stmt, FuncDecl):
                continue
            params = ", ".join(
                f"{self.c_param_type(pt)} {p.name}"
                for p, pt in zip(stmt.params, stmt.resolved_params)
            )
            self.block(f"{self.c_type(stmt.resolved_return)} {stmt.name}({params})")
            for s in stmt.body:
                self.emit_stmt(s)
            self.end_block()
            self.line()

    # ------------------------------------------------------------------ #
    # setup() / loop()                                                    #
    # ------------------------------------------------------------------ #

    def _emit_setup(self, setup_stmts):
        self.block("void setup()")
        for stmt in setup_stmts:
            if isinstance(stmt, VarDecl):
                value = stmt.value
                if value is None or self.is_constant(value):
                    continue
                ty = stmt.inferred_type
                if isinstance(ty, ArrayType):
                    if not isinstance(value, ArrayLit):
                        self.err("arrays can only be initialized with an array literal", stmt)
                    for i, elem in enumerate(value.elements):
                        self.line(f"{stmt.name}[{i}] = {self.emit_expr(elem)};")
                else:
                    self.line(f"{stmt.name} = {self.emit_expr(value)};")
            else:  # bare Init expression statement
                self.line(f"{self.emit_expr(stmt.expr)};")
        self.end_block()
        self.line()

    def _emit_loop(self, loop_stmts):
        self.block("void loop()")
        for stmt in loop_stmts:
            self.emit_stmt(stmt)
        self.end_block()

    # ------------------------------------------------------------------ #
    # Statements                                                          #
    # ------------------------------------------------------------------ #

    def emit_stmt(self, stmt):
        if isinstance(stmt, (ImportStmt, ModuleDecl)):
            return
        if isinstance(stmt, VarDecl):
            self.emit_local_var(stmt)
        elif isinstance(stmt, Assign):
            if isinstance(getattr(stmt.target, "inferred_type", None), ArrayType):
                self.err("cannot assign to the whole array", stmt)
            self.line(f"{self.emit_expr(stmt.target)} = {self.emit_expr(stmt.value)};")
        elif isinstance(stmt, ExprStmt):
            self.line(f"{self.emit_expr(stmt.expr)};")
        elif isinstance(stmt, ReturnStmt):
            if stmt.value is None:
                self.line("return;")
            else:
                self.line(f"return {self.emit_expr(stmt.value)};")
        elif isinstance(stmt, IfStmt):
            for i, branch in enumerate(stmt.branches):
                keyword = "if" if i == 0 else "else if"
                self.block(f"{keyword} ({self.emit_expr(branch.condition)})")
                for s in branch.body:
                    self.emit_stmt(s)
                self.end_block()
            if stmt.else_body is not None:
                self.line("else {")
                self.indent += 1
                for s in stmt.else_body:
                    self.emit_stmt(s)
                self.end_block()
        elif isinstance(stmt, WhileStmt):
            self.block(f"while ({self.emit_expr(stmt.condition)})")
            for s in stmt.body:
                self.emit_stmt(s)
            self.end_block()
        elif isinstance(stmt, ForStmt):
            self.emit_for(stmt)
        elif isinstance(stmt, BreakStmt):
            self.line("break;")
        elif isinstance(stmt, ContinueStmt):
            self.line("continue;")
        else:
            self.err(f"unsupported statement {type(stmt).__name__}", stmt)

    def emit_local_var(self, stmt):
        ty = stmt.inferred_type
        value = stmt.value
        if isinstance(ty, ArrayType):
            if ty.size is None:
                self.err("array variable has no fixed size", stmt)
            if value is not None and not isinstance(value, ArrayLit):
                self.err("arrays can only be initialized with an array literal", stmt)
            decl = f"{self.c_type(ty.element)} {stmt.name}[{ty.size}]"
            if value is not None and self.is_constant(value):
                elems = ", ".join(self.emit_expr(e) for e in value.elements)
                self.line(f"{decl} = {{{elems}}};")
            else:
                self.line(f"{decl};")
                if value is not None:
                    for i, elem in enumerate(value.elements):
                        self.line(f"{stmt.name}[{i}] = {self.emit_expr(elem)};")
        else:
            if value is not None and self.is_constant(value):
                self.line(f"{self.c_type(ty)} {stmt.name} = {self.emit_expr(value)};")
            else:
                self.line(f"{self.c_type(ty)} {stmt.name};")
                if value is not None:
                    self.line(f"{stmt.name} = {self.emit_expr(value)};")

    def emit_for(self, stmt):
        it = stmt.iterable
        itty = getattr(it, "inferred_type", None)
        if not (isinstance(it, Ident) and isinstance(itty, ArrayType)):
            self.err("for-loop must iterate over an array variable", stmt)
        if itty.size is None:
            self.err("cannot iterate over an array without a known size", stmt)
        idx = f"__volt_for_{self.for_counter}"
        self.for_counter += 1
        self.block(f"for (size_t {idx} = 0; {idx} < {itty.size}; ++{idx})")
        self.line(f"{self.c_type(itty.element)} {stmt.var_name} = {it.name}[{idx}];")
        for s in stmt.body:
            self.emit_stmt(s)
        self.end_block()

    # ------------------------------------------------------------------ #
    # Expressions                                                         #
    # ------------------------------------------------------------------ #

    def emit_expr(self, expr) -> str:
        if isinstance(expr, IntLit):
            return str(expr.value)
        if isinstance(expr, FloatLit):
            return self._float(expr.value)
        if isinstance(expr, BoolLit):
            return "true" if expr.value else "false"
        if isinstance(expr, StrLit):
            return self._c_string(expr.value)
        if isinstance(expr, NullLit):
            return "NULL"
        if isinstance(expr, Ident):
            return expr.name
        if isinstance(expr, Unary):
            if expr.op == "-":
                return f"(-{self.emit_expr(expr.operand)})"
            return f"(!{self.emit_expr(expr.operand)})"
        if isinstance(expr, Binary):
            return self.emit_binary(expr)
        if isinstance(expr, Member):
            # Only module constants reach codegen as values (sema rejects the rest).
            return expr.name
        if isinstance(expr, Call):
            return self.emit_call(expr)
        if isinstance(expr, Index):
            return f"{self.emit_expr(expr.obj)}[{self.emit_expr(expr.index)}]"
        if isinstance(expr, ArrayLit):
            return "{" + ", ".join(self.emit_expr(e) for e in expr.elements) + "}"
        self.err(f"unsupported expression {type(expr).__name__}", expr)

    def emit_binary(self, node) -> str:
        op = node.op
        left = self.emit_expr(node.left)
        right = self.emit_expr(node.right)
        if op in ("and", "or"):
            c_op = "&&" if op == "and" else "||"
            return f"(({left}) {c_op} ({right}))"
        if op in ("==", "!="):
            lt = getattr(node.left, "inferred_type", None)
            rt = getattr(node.right, "inferred_type", None)
            if lt is STRING or rt is STRING:
                cmp = "== 0" if op == "==" else "!= 0"
                return f"(strcmp({left}, {right}) {cmp})"
        return f"({left} {op} {right})"

    def emit_call(self, node) -> str:
        callee = node.callee
        if isinstance(callee, Member):
            obj_ty = getattr(callee.obj, "inferred_type", None)
            if isinstance(obj_ty, ModuleType) and callee.name == "Init":
                storage = getattr(node, "_volt_storage", None)
                if storage is None:
                    self.err("internal error: Init call without storage", node)
                helper = INIT_HELPERS[obj_ty.name]
                args = [f"&{storage}"] + [self.emit_expr(a) for a in node.args]
                return f"{helper}({', '.join(args)})"
            return self.emit_method_call(node, callee, obj_ty)
        if isinstance(callee, Ident):
            name = callee.name
            args = ", ".join(self.emit_expr(a) for a in node.args)
            if name in BUILTIN_C:
                return f"{BUILTIN_C[name]}({args})"
            return f"{name}({args})"
        self.err("unsupported callee", node)

    def emit_method_call(self, node, callee, obj_ty) -> str:
        obj = self.emit_expr(callee.obj)
        method = callee.name
        args = [self.emit_expr(a) for a in node.args]
        if not isinstance(obj_ty, ModuleType):
            self.err("cannot call a method on a non-handle value", node)
        mod = obj_ty.name
        if mod in STATIC_MODULES:
            return f"{mod}.{method}({', '.join(args)})"
        if mod == "Servo":
            return f"{obj}->{method}({', '.join(args)})"
        if mod == "DigitalPin":
            if method == "write":
                return f"digitalWrite({obj}->pin, {args[0]})"
            if method == "read":
                return f"digitalRead({obj}->pin)"
        if mod == "AnalogPin":
            if method == "read":
                return f"analogRead({obj}->pin)"
            if method == "write":
                return f"analogWrite({obj}->pin, {args[0]})"
        self.err(f"cannot translate method '{mod}.{method}' to C", node)

    # ------------------------------------------------------------------ #
    # Types                                                               #
    # ------------------------------------------------------------------ #

    def c_type(self, t) -> str:
        if t is INT:
            return "int16_t"
        if t is FLOAT:
            return "float"
        if t is BOOL:
            return "bool"
        if t is STRING:
            return "const char*"
        if t is VOID:
            return "void"
        if t is NULL:
            return "void*"
        if isinstance(t, ArrayType):
            if t.size is None:
                self.err("array type has no fixed size")
            return f"{self.c_type(t.element)}[{t.size}]"
        if isinstance(t, ModuleType):
            return HANDLE_C_TYPES[t.name]
        self.err(f"cannot translate type {t} to C")

    def c_param_type(self, t) -> str:
        if isinstance(t, ArrayType):
            return f"{self.c_type(t.element)}*"
        return self.c_type(t)

    # ------------------------------------------------------------------ #
    # Constant folding helpers                                            #
    # ------------------------------------------------------------------ #

    def is_constant(self, expr) -> bool:
        if isinstance(expr, (IntLit, FloatLit, BoolLit, StrLit, NullLit)):
            return True
        if isinstance(expr, Unary) and expr.op == "-":
            return self.is_constant(expr.operand)
        if isinstance(expr, Member) and isinstance(expr.obj, Ident):
            return True     # module constant
        if isinstance(expr, ArrayLit):
            return all(self.is_constant(e) for e in expr.elements)
        return False

    @staticmethod
    def _c_string(s: str) -> str:
        out = ['"']
        for byte in s.encode("utf-8"):
            if byte == 34:
                out.append('\\"')
            elif byte == 92:
                out.append("\\\\")
            elif 32 <= byte <= 126:
                out.append(chr(byte))
            else:
                out.append("\\%03o" % byte)
        out.append('"')
        return "".join(out)

    @staticmethod
    def _float(v: float) -> str:
        return repr(v) + "f"


def codegen(program: Program, filename="<string>") -> str:
    """Convenience wrapper around :class:`CCodeGen`."""
    return CCodeGen(filename).generate(program)
