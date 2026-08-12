"""Semantic analyzer for Volt.

Performs, in one pass over a :class:`volt.ast.Program` (after a first pass
collects function signatures):

* Scope / variable resolution with block scoping, shadowing warnings, and
  unused-variable warnings.
* Static type checking and type inference. Inferred types are attached to AST
  nodes as ``node.inferred_type`` for the code generator.
* Validation of hardware module usage: known modules, known methods/constants,
  Init() argument counts/types, literal pin ranges, and DigitalPin modes.
* Loop nesting checks for ``break``/``continue`` and ``return`` placement.

Diagnostics are collected (never raised) so a whole file can be reported at
once; ``Analyzer.analyze`` returns ``False`` if any error was found.
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
)
from .types import (
    Type,
    INT,
    FLOAT,
    BOOL,
    STRING,
    VOID,
    NULL,
    ERROR,
    ArrayType,
    ModuleType,
    Callable,
    is_numeric,
    is_truthy,
)
from .modules import MODULES, BUILTIN_FUNCTIONS

PRIMITIVES = {"int": INT, "float": FLOAT, "bool": BOOL, "string": STRING}

# Global names that would collide with the generated C entry points (setup/loop)
# or the Arduino runtime (main, millis, micros, delay, ...).
RESERVED_NAMES = {
    "setup", "loop", "init", "main",
    "millis", "micros", "delay", "delayMicroseconds",
}


class Diagnostic:
    """A single semantic error or warning."""

    def __init__(self, message, line, col, filename, severity="error"):
        self.message = message
        self.line = line
        self.col = col
        self.filename = filename
        self.severity = severity

    @property
    def is_error(self):
        return self.severity == "error"

    def __str__(self):
        return f"{self.filename}:{self.line}:{self.col}: {self.severity}: {self.message}"


class Symbol:
    def __init__(self, name, type, kind, line=0, col=0, callable=None, const=False):
        self.name = name
        self.type = type
        self.kind = kind            # "var" | "param" | "func" | "module"
        self.line = line
        self.col = col
        self.callable = callable    # Callable for "func" symbols
        self.const = const
        self.used = False


class Scope:
    def __init__(self, parent=None):
        self.parent = parent
        self.symbols = {}

    def lookup_local(self, name):
        return self.symbols.get(name)

    def lookup(self, name):
        scope = self
        while scope is not None:
            if name in scope.symbols:
                return scope.symbols[name]
            scope = scope.parent
        return None

    def is_global(self):
        return self.parent is None


class Analyzer:
    def __init__(self, filename="<string>"):
        self.filename = filename
        self.diagnostics = []
        self.scope = None
        self.scopes = []
        self.functions = {}         # name -> FuncDecl (with .callable set)
        self.return_type = None     # None = not in a function; VOID/Type otherwise
        self.function_name = None
        self.loop_depth = 0

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def analyze(self, program: Program) -> bool:
        """Analyze the program. Returns True if there were no errors."""
        self.collect_functions(program.statements)

        self.scope = Scope()
        self.scopes.append(self.scope)
        for name, mod in MODULES.items():
            self.scope.symbols[name] = Symbol(name, mod, "module")
        for name, fn in BUILTIN_FUNCTIONS.items():
            self.scope.symbols[name] = Symbol(name, fn, "func", callable=fn)
        for name, fdecl in self.functions.items():
            self.declare(Symbol(name, None, "func", line=fdecl.line, col=fdecl.col, callable=fdecl.callable))

        for stmt in program.statements:
            self.analyze_stmt(stmt)

        self.emit_unused_warnings()
        return self.has_errors() is False

    def has_errors(self) -> bool:
        return any(d.is_error for d in self.diagnostics)

    # ------------------------------------------------------------------ #
    # Diagnostics / scopes                                                #
    # ------------------------------------------------------------------ #

    def error(self, message, line, col):
        self.diagnostics.append(Diagnostic(message, line, col, self.filename, "error"))

    def warning(self, message, line, col):
        self.diagnostics.append(Diagnostic(message, line, col, self.filename, "warning"))

    def declare(self, symbol) -> bool:
        """Declare in the current scope; detect duplicates and shadowing."""
        local = self.scope.lookup_local(symbol.name)
        if local is not None:
            self.error(
                f"'{symbol.name}' is already defined in this scope",
                symbol.line, symbol.col,
            )
            return False
        outer = None
        s = self.scope.parent
        while s is not None:
            if symbol.name in s.symbols:
                outer = s.symbols[symbol.name]
                break
            s = s.parent
        if outer is not None and symbol.kind in ("var", "param") and not outer.const:
            self.warning(
                f"'{symbol.name}' shadows an outer definition",
                symbol.line, symbol.col,
            )
        self.scope.symbols[symbol.name] = symbol
        return True

    def push_scope(self):
        scope = Scope(self.scope)
        self.scope = scope
        self.scopes.append(scope)
        return scope

    def pop_scope(self):
        self.scope = self.scope.parent

    def emit_unused_warnings(self):
        for scope in self.scopes:
            if scope.is_global():
                continue
            for sym in scope.symbols.values():
                if sym.kind in ("var", "param") and not sym.used:
                    self.warning(f"unused variable '{sym.name}'", sym.line, sym.col)

    # ------------------------------------------------------------------ #
    # Pass 1: function signatures                                         #
    # ------------------------------------------------------------------ #

    def collect_functions(self, statements):
        for stmt in statements:
            if isinstance(stmt, FuncDecl):
                if stmt.name in RESERVED_NAMES:
                    self.error(
                        f"'{stmt.name}' is a reserved name and cannot be used as a function",
                        stmt.line, stmt.col,
                    )
                    continue
                if stmt.name in self.functions:
                    self.error(f"function '{stmt.name}' is already defined", stmt.line, stmt.col)
                    continue
                self.functions[stmt.name] = stmt
                params = []
                for p in stmt.params:
                    if p.type is None:
                        params.append((p.name, ERROR))
                    else:
                        params.append((p.name, self.resolve_type_ref(p.type)))
                ret = VOID if stmt.return_type is None else self.resolve_type_ref(stmt.return_type)
                stmt.callable = Callable(stmt.name, params, ret)
                stmt.resolved_return = ret
                stmt.resolved_params = [t for _, t in params]

    # ------------------------------------------------------------------ #
    # Statements                                                          #
    # ------------------------------------------------------------------ #

    def analyze_stmt(self, stmt):
        if isinstance(stmt, ImportStmt):
            if stmt.name not in MODULES:
                self.error(f"unknown module '{stmt.name}' (known modules: {', '.join(sorted(MODULES))})", stmt.line, stmt.col)
        elif isinstance(stmt, ModuleDecl):
            self.analyze_module(stmt)
        elif isinstance(stmt, FuncDecl):
            self.analyze_func(stmt)
        elif isinstance(stmt, VarDecl):
            self.analyze_var(stmt)
        elif isinstance(stmt, Assign):
            self.analyze_assign(stmt)
        elif isinstance(stmt, ExprStmt):
            self.analyze_expr(stmt.expr)
        elif isinstance(stmt, ReturnStmt):
            self.analyze_return(stmt)
        elif isinstance(stmt, IfStmt):
            self.analyze_if(stmt)
        elif isinstance(stmt, WhileStmt):
            self.analyze_while(stmt)
        elif isinstance(stmt, ForStmt):
            self.analyze_for(stmt)
        elif isinstance(stmt, BreakStmt):
            if self.loop_depth == 0:
                self.error("'break' is only allowed inside a loop", stmt.line, stmt.col)
        elif isinstance(stmt, ContinueStmt):
            if self.loop_depth == 0:
                self.error("'continue' is only allowed inside a loop", stmt.line, stmt.col)

    def analyze_module(self, stmt):
        self.warning(
            "user-defined modules are not supported in v1; "
            "body is analyzed in the current scope",
            stmt.line, stmt.col,
        )
        self.push_scope()
        for s in stmt.body:
            self.analyze_stmt(s)
        self.pop_scope()

    def analyze_func(self, stmt):
        ret = getattr(stmt, "resolved_return", VOID)
        self.push_scope()
        saved_return = self.return_type
        saved_name = self.function_name
        self.return_type = ret
        self.function_name = stmt.name
        stmt.inferred_type = ret

        for p, ptype in zip(stmt.params, getattr(stmt, "resolved_params", [None] * len(stmt.params))):
            if p.type is None:
                self.error(
                    f"parameter '{p.name}' requires a type annotation in v1",
                    p.line, p.col,
                )
            p.inferred_type = ptype
            self.declare(Symbol(p.name, ptype, "param", line=p.line, col=p.col))

        has_return = False
        for s in stmt.body:
            self.analyze_stmt(s)
            if isinstance(s, ReturnStmt):
                has_return = True

        if ret is not VOID and ret is not ERROR and not has_return:
            self.warning(
                f"function '{stmt.name}' is declared to return {ret} "
                "but never returns a value",
                stmt.line, stmt.col,
            )

        self.return_type = saved_return
        self.function_name = saved_name
        self.pop_scope()

    def analyze_var(self, stmt):
        if self.scope.is_global() and stmt.name in RESERVED_NAMES:
            self.error(
                f"'{stmt.name}' is a reserved name and cannot be used as a variable",
                stmt.line, stmt.col,
            )

        declared = None
        if stmt.type is not None:
            declared = self.resolve_type_ref(stmt.type)

        if stmt.value is not None:
            if (
                declared is not None
                and isinstance(declared, ArrayType)
                and isinstance(stmt.value, ArrayLit)
                and not stmt.value.elements
            ):
                # Empty literal against a declared array: no elements to analyze.
                if declared.size is None:
                    self.error(
                        f"cannot determine the size of the empty array for '{stmt.name}'; "
                        "add a size like int[10]",
                        stmt.line, stmt.col,
                    )
                    declared = ArrayType(declared.element, 0)
                vtype = declared
                stmt.value.inferred_type = vtype
            elif (
                declared is not None
                and isinstance(declared, ArrayType)
                and isinstance(stmt.value, ArrayLit)
            ):
                vtype = self.analyze_array(stmt.value)
                if declared.size is None and isinstance(vtype, ArrayType) and vtype.size:
                    declared = ArrayType(declared.element, vtype.size)
                stmt.value.inferred_type = declared
            else:
                vtype = self.analyze_expr(stmt.value)
                if vtype is VOID:
                    self.error(
                        "cannot use a void value in a variable declaration",
                        stmt.line, stmt.col,
                    )
                    vtype = ERROR
            if declared is None:
                declared = vtype
                if declared is NULL:
                    self.error(
                        f"cannot infer the type of '{stmt.name}' from null; "
                        "add a type annotation",
                        stmt.line, stmt.col,
                    )
                    declared = ERROR
        else:
            if declared is None:
                self.error(
                    f"variable '{stmt.name}' requires a type annotation "
                    "or an initializer",
                    stmt.line, stmt.col,
                )
                declared = ERROR

        if isinstance(declared, ArrayType) and declared.size is None:
            self.error(
                f"array variable '{stmt.name}' needs a fixed size or an initializer",
                stmt.line, stmt.col,
            )

        if stmt.value is not None and not self.is_assignable(declared, vtype):
            self.error(
                f"cannot assign {vtype} to variable '{stmt.name}' of type {declared}",
                stmt.line, stmt.col,
            )

        self.declare(Symbol(stmt.name, declared, "var", line=stmt.line, col=stmt.col))
        stmt.inferred_type = declared

    def analyze_assign(self, stmt):
        value_ty = self.analyze_expr(stmt.value)
        target = stmt.target

        if isinstance(target, Ident):
            sym = self.scope.lookup(target.name)
            if sym is None:
                self.error(f"undefined variable '{target.name}'", target.line, target.col)
            elif sym.kind != "var":
                self.error(f"cannot assign to '{target.name}'", target.line, target.col)
            elif isinstance(sym.type, ArrayType):
                self.error(
                    f"cannot assign to the whole array '{target.name}'; "
                    "assign to elements instead",
                    target.line, target.col,
                )
            else:
                target.inferred_type = sym.type
                if not self.is_assignable(sym.type, value_ty):
                    self.error(
                        f"cannot assign {value_ty} to '{target.name}' of type {sym.type}",
                        stmt.line, stmt.col,
                    )
        elif isinstance(target, Member):
            obj_ty = self.analyze_expr(target.obj)
            if isinstance(obj_ty, ModuleType):
                if target.name in obj_ty.constants:
                    self.error(
                        f"cannot assign to constant '{obj_ty.name}.{target.name}'",
                        stmt.line, stmt.col,
                    )
                elif target.name in obj_ty.methods:
                    self.error(
                        f"cannot assign to method '{obj_ty.name}.{target.name}'",
                        stmt.line, stmt.col,
                    )
                else:
                    self.error(
                        f"{obj_ty.name} has no member '{target.name}'",
                        stmt.line, stmt.col,
                    )
            else:
                self.error("cannot assign to a member of a non-module value", stmt.line, stmt.col)
        elif isinstance(target, Index):
            obj_ty = self.analyze_expr(target.obj)
            idx_ty = self.analyze_expr(target.index)
            if isinstance(obj_ty, ArrayType):
                if idx_ty is not INT and idx_ty is not ERROR:
                    self.error("array index must be an int", target.index.line, target.index.col)
                if not self.is_assignable(obj_ty.element, value_ty):
                    self.error(
                        f"cannot assign {value_ty} to an array element of type {obj_ty.element}",
                        stmt.line, stmt.col,
                    )
            elif obj_ty is not ERROR:
                self.error(f"cannot index a value of type {obj_ty}", stmt.line, stmt.col)
        else:
            self.error("invalid assignment target", stmt.line, stmt.col)

    def analyze_return(self, stmt):
        if self.return_type is None:
            self.error("'return' is not allowed at the top level", stmt.line, stmt.col)
            if stmt.value is not None:
                self.analyze_expr(stmt.value)
            return
        if self.return_type is VOID:
            if stmt.value is not None:
                self.error(
                    f"function '{self.function_name}' does not return a value "
                    "but 'return' provides one",
                    stmt.line, stmt.col,
                )
                self.analyze_expr(stmt.value)
            return
        if stmt.value is None:
            self.error(
                f"function '{self.function_name}' returns {self.return_type} "
                "but 'return' has no value",
                stmt.line, stmt.col,
            )
            return
        vty = self.analyze_expr(stmt.value)
        if not self.is_assignable(self.return_type, vty):
            self.error(
                f"cannot return {vty} from a function returning {self.return_type}",
                stmt.line, stmt.col,
            )

    def analyze_if(self, stmt):
        for branch in stmt.branches:
            self.require_truthy(branch.condition, f"{branch.keyword} condition")
            self.push_scope()
            for s in branch.body:
                self.analyze_stmt(s)
            self.pop_scope()
        if stmt.else_body is not None:
            self.push_scope()
            for s in stmt.else_body:
                self.analyze_stmt(s)
            self.pop_scope()

    def analyze_while(self, stmt):
        self.require_truthy(stmt.condition, "while condition")
        self.loop_depth += 1
        self.push_scope()
        for s in stmt.body:
            self.analyze_stmt(s)
        self.pop_scope()
        self.loop_depth -= 1

    def analyze_for(self, stmt):
        iter_ty = self.analyze_expr(stmt.iterable)
        if isinstance(iter_ty, ArrayType):
            elem = iter_ty.element
        elif iter_ty is ERROR:
            elem = ERROR
        else:
            self.error(f"cannot iterate over a value of type {iter_ty}", stmt.line, stmt.col)
            elem = ERROR
        self.loop_depth += 1
        self.push_scope()
        self.declare(Symbol(stmt.var_name, elem, "var", line=stmt.line, col=stmt.col))
        for s in stmt.body:
            self.analyze_stmt(s)
        self.pop_scope()
        self.loop_depth -= 1

    # ------------------------------------------------------------------ #
    # Types                                                               #
    # ------------------------------------------------------------------ #

    def resolve_type_ref(self, tref) -> Type:
        if tref.array:
            elem = self.resolve_base(tref.base, tref.line, tref.col)
            return ArrayType(elem, tref.size)
        return self.resolve_base(tref.base, tref.line, tref.col)

    def resolve_base(self, name, line, col) -> Type:
        if name in PRIMITIVES:
            return PRIMITIVES[name]
        if name in MODULES:
            return MODULES[name]
        self.error(f"unknown type '{name}'", line, col)
        return ERROR

    def is_assignable(self, target: Type, value: Type) -> bool:
        if target is ERROR or value is ERROR:
            return True
        if target is value:
            return True
        if target is FLOAT and value is INT:      # safe widening
            return True
        if value is NULL and isinstance(target, (ModuleType, ArrayType)):
            return True
        if isinstance(target, ArrayType) and isinstance(value, ArrayType):
            return self.is_assignable(target.element, value.element)
        if isinstance(target, ModuleType) and isinstance(value, ModuleType):
            return target.name == value.name
        return False

    def require_truthy(self, expr, what):
        ty = self.analyze_expr(expr)
        if ty is ERROR:
            return
        if not is_truthy(ty):
            self.error(
                f"{what} must be a bool, int, float, or handle value, got {ty}",
                expr.line, expr.col,
            )

    # ------------------------------------------------------------------ #
    # Expressions                                                         #
    # ------------------------------------------------------------------ #

    def analyze_expr(self, expr) -> Type:
        if isinstance(expr, IntLit):
            expr.inferred_type = INT
            return INT
        if isinstance(expr, FloatLit):
            expr.inferred_type = FLOAT
            return FLOAT
        if isinstance(expr, BoolLit):
            expr.inferred_type = BOOL
            return BOOL
        if isinstance(expr, StrLit):
            expr.inferred_type = STRING
            return STRING
        if isinstance(expr, NullLit):
            expr.inferred_type = NULL
            return NULL
        if isinstance(expr, Ident):
            return self.analyze_ident(expr)
        if isinstance(expr, Member):
            return self.analyze_member(expr)
        if isinstance(expr, Call):
            return self.analyze_call(expr)
        if isinstance(expr, Unary):
            return self.analyze_unary(expr)
        if isinstance(expr, Binary):
            return self.analyze_binary(expr)
        if isinstance(expr, Index):
            return self.analyze_index(expr)
        if isinstance(expr, ArrayLit):
            return self.analyze_array(expr)
        expr.inferred_type = ERROR
        return ERROR

    def analyze_ident(self, expr):
        sym = self.scope.lookup(expr.name)
        if sym is None:
            self.error(f"undefined identifier '{expr.name}'", expr.line, expr.col)
            expr.inferred_type = ERROR
            return ERROR
        sym.used = True
        if sym.kind == "func":
            self.error(
                f"function '{expr.name}' cannot be used as a value "
                "(first-class functions are not supported in v1)",
                expr.line, expr.col,
            )
            expr.inferred_type = ERROR
            return ERROR
        expr.inferred_type = sym.type
        return sym.type

    def analyze_member(self, expr):
        obj_ty = self.analyze_expr(expr.obj)
        if not isinstance(obj_ty, ModuleType):
            if obj_ty is not ERROR:
                self.error(f"type {obj_ty} has no member '{expr.name}'", expr.line, expr.col)
            expr.inferred_type = ERROR
            return ERROR
        if expr.name in obj_ty.constants:
            const = obj_ty.constants[expr.name]
            expr.inferred_type = const.type
            return const.type
        if expr.name in obj_ty.methods:
            self.error(
                f"method '{obj_ty.name}.{expr.name}' cannot be used as a value "
                "(first-class functions are not supported in v1)",
                expr.line, expr.col,
            )
            expr.inferred_type = ERROR
            return ERROR
        self.error(f"{obj_ty.name} has no member '{expr.name}'", expr.line, expr.col)
        expr.inferred_type = ERROR
        return ERROR

    def analyze_call(self, node):
        callee = node.callee

        if isinstance(callee, Member):
            obj_ty = self.analyze_expr(callee.obj)
            if not isinstance(obj_ty, ModuleType):
                if obj_ty is not ERROR:
                    self.error("cannot call a member of a non-module value", node.line, node.col)
                self.analyze_args(node.args)
                node.inferred_type = ERROR
                return ERROR
            name = callee.name
            if name in obj_ty.constants:
                self.error(f"cannot call constant '{obj_ty.name}.{name}'", node.line, node.col)
                self.analyze_args(node.args)
                node.inferred_type = ERROR
                return ERROR
            if name not in obj_ty.methods:
                self.error(f"{obj_ty.name} has no method '{name}'", node.line, node.col)
                self.analyze_args(node.args)
                node.inferred_type = ERROR
                return ERROR
            if obj_ty.name == "Serial" and name in ("print", "println"):
                self.check_serial_print(obj_ty, name, node)
                node.inferred_type = VOID
                return VOID
            fn = obj_ty.methods[name]
            ret = self.check_call(f"{obj_ty.name}.{name}", fn, node.args, node)
            if name == "Init":
                self.validate_init(obj_ty, node)
                node.inferred_type = obj_ty
                return obj_ty
            node.inferred_type = ret
            return ret

        if isinstance(callee, Ident):
            sym = self.scope.lookup(callee.name)
            if sym is None:
                self.error(f"undefined function '{callee.name}'", node.line, node.col)
                self.analyze_args(node.args)
                node.inferred_type = ERROR
                return ERROR
            if sym.kind == "func":
                ret = self.check_call(callee.name, sym.callable, node.args, node)
                node.inferred_type = ret
                return ret
            if sym.kind == "module":
                self.error(f"module '{callee.name}' cannot be called", node.line, node.col)
                self.analyze_args(node.args)
                node.inferred_type = ERROR
                return ERROR
            self.error(f"'{callee.name}' is not a function", node.line, node.col)
            self.analyze_args(node.args)
            node.inferred_type = ERROR
            return ERROR

        self.error("cannot call this expression", node.line, node.col)
        self.analyze_args(node.args)
        node.inferred_type = ERROR
        return ERROR

    def check_call(self, name, callable_, args, node):
        if len(args) != len(callable_.params):
            self.error(
                f"{name} expects {len(callable_.params)} argument(s), got {len(args)}",
                node.line, node.col,
            )
            self.analyze_args(args)
            return callable_.return_type
        for i, (pname, ptype) in enumerate(callable_.params):
            aty = self.analyze_expr(args[i])
            if not self.is_assignable(ptype, aty):
                self.error(
                    f"argument {i + 1} ({pname}) of {name}: expected {ptype}, got {aty}",
                    args[i].line, args[i].col,
                )
        return callable_.return_type

    def check_serial_print(self, module: ModuleType, name, node):
        """Serial.print/println accept any printable primitive."""
        if len(node.args) != 1:
            self.error(
                f"{module.name}.{name} expects 1 argument, got {len(node.args)}",
                node.line, node.col,
            )
            self.analyze_args(node.args)
            return
        aty = self.analyze_expr(node.args[0])
        if aty is not ERROR and aty not in (INT, FLOAT, STRING, BOOL):
            self.error(
                f"argument 1 (value) of {module.name}.{name}: expected int, "
                f"float, string, or bool, got {aty}",
                node.args[0].line, node.args[0].col,
            )

    def analyze_args(self, args):
        for a in args:
            self.analyze_expr(a)

    def validate_init(self, module: ModuleType, node):
        """Compile-time checks on Init() arguments when they are literals."""
        args = node.args
        if module.pin_range is not None and len(args) >= 1:
            pin = self.const_int(args[0])
            if pin is not None and not (module.pin_range[0] <= pin <= module.pin_range[1]):
                self.error(
                    f"pin {pin} is out of range for {module.name}.Init() "
                    f"(valid range: {module.pin_range[0]}-{module.pin_range[1]})",
                    args[0].line, args[0].col,
                )
        if module.modes is not None and len(args) >= 2:
            mode = self.const_int(args[1])
            if mode is not None and mode not in module.modes:
                self.error(
                    f"invalid mode {mode} for DigitalPin.Init() "
                    f"(expected one of: {', '.join(str(m) for m in sorted(module.modes))})",
                    args[1].line, args[1].col,
                )

    def const_int(self, expr):
        """Return the integer value of a compile-time constant expression."""
        if isinstance(expr, IntLit):
            return expr.value
        if isinstance(expr, Unary) and expr.op == "-" and isinstance(expr.operand, IntLit):
            return -expr.operand.value
        if isinstance(expr, Member) and isinstance(expr.obj, Ident):
            sym = self.scope.lookup(expr.obj.name)
            if sym is not None and isinstance(sym.type, ModuleType):
                const = sym.type.constants.get(expr.name)
                if const is not None and isinstance(const.value, int):
                    return const.value
        return None

    def analyze_unary(self, node):
        ot = self.analyze_expr(node.operand)
        if node.op == "-":
            if ot is ERROR:
                node.inferred_type = ERROR
                return ERROR
            if is_numeric(ot):
                node.inferred_type = ot
                return ot
            self.error(f"unary '-' requires a numeric operand, got {ot}", node.line, node.col)
            node.inferred_type = ERROR
            return ERROR
        if ot is ERROR:
            node.inferred_type = ERROR
            return ERROR
        if is_truthy(ot):
            node.inferred_type = BOOL
            return BOOL
        self.error(f"unary '{node.op}' requires a bool, int, float, or handle operand, got {ot}", node.line, node.col)
        node.inferred_type = ERROR
        return ERROR

    def analyze_binary(self, node):
        lt = self.analyze_expr(node.left)
        rt = self.analyze_expr(node.right)
        op = node.op

        if op in ("and", "or"):
            for operand, which in ((node.left, "left"), (node.right, "right")):
                ty = lt if which == "left" else rt
                if ty is not ERROR and not is_truthy(ty):
                    self.error(
                        f"{which} operand of '{op}' must be a bool, int, float, or handle, got {ty}",
                        operand.line, operand.col,
                    )
            node.inferred_type = BOOL
            return BOOL

        if op in ("==", "!="):
            if not (lt is ERROR or rt is ERROR) and not self.compatible_eq(lt, rt):
                self.error(f"cannot compare {lt} and {rt} with '{op}'", node.line, node.col)
            node.inferred_type = BOOL
            return BOOL

        if op in ("<", ">", "<=", ">="):
            if not (lt is ERROR or rt is ERROR) and not self.compatible_order(lt, rt):
                self.error(f"cannot compare {lt} and {rt} with '{op}'", node.line, node.col)
            node.inferred_type = BOOL
            return BOOL

        if op in ("+", "-", "*", "/"):
            if lt is ERROR or rt is ERROR:
                node.inferred_type = ERROR
                return ERROR
            if is_numeric(lt) and is_numeric(rt):
                t = FLOAT if (lt is FLOAT or rt is FLOAT) else INT
                node.inferred_type = t
                return t
            self.error(f"operator '{op}' requires numeric operands, got {lt} and {rt}", node.line, node.col)
            node.inferred_type = ERROR
            return ERROR

        if op == "%":
            if lt is ERROR or rt is ERROR:
                node.inferred_type = ERROR
                return ERROR
            if lt is INT and rt is INT:
                node.inferred_type = INT
                return INT
            self.error(f"operator '%' requires int operands, got {lt} and {rt}", node.line, node.col)
            node.inferred_type = ERROR
            return ERROR

        node.inferred_type = ERROR
        return ERROR

    def compatible_eq(self, a, b):
        if a is ERROR or b is ERROR:
            return True
        if a is b:
            return True
        if is_numeric(a) and is_numeric(b):
            return True
        if a is NULL or b is NULL:
            return True
        if isinstance(a, ArrayType) and isinstance(b, ArrayType):
            return self.is_assignable(a.element, b.element)
        if isinstance(a, ModuleType) and isinstance(b, ModuleType) and a.name == b.name:
            return True
        return False

    def compatible_order(self, a, b):
        if a is ERROR or b is ERROR:
            return True
        if is_numeric(a) and is_numeric(b):
            return True
        if a is STRING and b is STRING:
            return True
        return False

    def analyze_index(self, node):
        ot = self.analyze_expr(node.obj)
        it = self.analyze_expr(node.index)
        if isinstance(ot, ArrayType):
            if it is not INT and it is not ERROR:
                self.error("array index must be an int", node.index.line, node.index.col)
            node.inferred_type = ot.element
            return ot.element
        if ot is not ERROR:
            self.error(f"cannot index a value of type {ot}", node.line, node.col)
        node.inferred_type = ERROR
        return ERROR

    def analyze_array(self, node):
        elem = None
        for e in node.elements:
            t = self.analyze_expr(e)
            if elem is None:
                elem = t
            elif t is not ERROR and elem is not ERROR and not (is_numeric(elem) and is_numeric(t)):
                if elem is not t:
                    self.error(
                        f"array literal elements must all have the same type "
                        f"(found {elem} and {t})",
                        node.line, node.col,
                    )
                    elem = ERROR
        if not node.elements:
            self.error(
                "cannot infer the element type of an empty array literal "
                "(add a type annotation)",
                node.line, node.col,
            )
            elem = ERROR
        if elem is None:
            elem = ERROR
        ty = ArrayType(elem, len(node.elements))
        node.inferred_type = ty
        return ty


def analyze(program: Program, filename="<string>") -> bool:
    """Convenience wrapper around :class:`Analyzer`."""
    return Analyzer(filename).analyze(program)
