"""Abstract Syntax Tree node definitions for Volt.

Every node carries its source position (``line``, ``col``) so later stages
can produce accurate diagnostics. ``Stmt`` and ``Expr`` are used as loose
type aliases for readability; see ``voltc.py`` for the full pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union


@dataclass
class Node:
    """Base class: all AST nodes have a source position."""

    line: int
    col: int


# --------------------------------------------------------------------------- #
# Top-level                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class Program(Node):
    statements: list = field(default_factory=list)


@dataclass
class ImportStmt(Node):
    name: str


@dataclass
class ModuleDecl(Node):
    name: str
    body: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Functions / parameters                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class Param(Node):
    name: str
    type: Optional["TypeRef"] = None


@dataclass
class FuncDecl(Node):
    name: str
    params: list = field(default_factory=list)
    return_type: Optional["TypeRef"] = None
    body: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Statements                                                                  #
# --------------------------------------------------------------------------- #


@dataclass
class VarDecl(Node):
    name: str
    type: Optional["TypeRef"] = None
    value: Optional["Expr"] = None


@dataclass
class Assign(Node):
    target: "Expr"
    value: "Expr"


@dataclass
class ExprStmt(Node):
    expr: "Expr"


@dataclass
class ReturnStmt(Node):
    value: Optional["Expr"] = None


@dataclass
class IfBranch(Node):
    keyword: str  # "if" | "elif"
    condition: "Expr"
    body: list = field(default_factory=list)


@dataclass
class IfStmt(Node):
    branches: list = field(default_factory=list)  # list[IfBranch]
    else_body: Optional[list] = None


@dataclass
class WhileStmt(Node):
    condition: "Expr"
    body: list = field(default_factory=list)


@dataclass
class ForStmt(Node):
    var_name: str
    iterable: "Expr"
    body: list = field(default_factory=list)


@dataclass
class BreakStmt(Node):
    pass


@dataclass
class ContinueStmt(Node):
    pass


# --------------------------------------------------------------------------- #
# Types                                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class TypeRef(Node):
    base: str                        # "int", "float", "bool", "string", "Servo", ...
    array: bool = False              # True -> "base[]" (or "base[N]")
    size: Optional[int] = None       # fixed-size array capacity, if given


def type_str(t: Optional[TypeRef]) -> str:
    if t is None:
        return "<unknown>"
    s = t.base
    if t.array:
        s += "[]" if t.size is None else f"[{t.size}]"
    return s


# --------------------------------------------------------------------------- #
# Expressions                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class IntLit(Node):
    value: int


@dataclass
class FloatLit(Node):
    value: float


@dataclass
class BoolLit(Node):
    value: bool


@dataclass
class StrLit(Node):
    value: str


@dataclass
class NullLit(Node):
    pass


@dataclass
class Ident(Node):
    name: str


@dataclass
class Unary(Node):
    op: str          # "-" | "!" | "not"
    operand: "Expr"


@dataclass
class Binary(Node):
    op: str
    left: "Expr"
    right: "Expr"


@dataclass
class Call(Node):
    callee: "Expr"
    args: list = field(default_factory=list)


@dataclass
class Member(Node):
    obj: "Expr"
    name: str


@dataclass
class Index(Node):
    obj: "Expr"
    index: "Expr"


@dataclass
class ArrayLit(Node):
    elements: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# AST dumping                                                                 #
# --------------------------------------------------------------------------- #

Stmt = Any
Expr = Any


def ast_label(node: Node) -> str:
    """Short human-readable label for a node in an AST dump."""
    if isinstance(node, Program):
        return "Program"
    if isinstance(node, ImportStmt):
        return f"Import({node.name})"
    if isinstance(node, ModuleDecl):
        return f"Module({node.name})"
    if isinstance(node, FuncDecl):
        return f"FuncDecl({node.name})"
    if isinstance(node, Param):
        return f"Param({node.name}: {type_str(node.type)})" if node.type else f"Param({node.name})"
    if isinstance(node, TypeRef):
        return f"Type({type_str(node)})"
    if isinstance(node, VarDecl):
        return (
            f"VarDecl({node.name}: {type_str(node.type)})"
            if node.type
            else f"VarDecl({node.name})"
        )
    if isinstance(node, Assign):
        return "Assign"
    if isinstance(node, ExprStmt):
        return "ExprStmt"
    if isinstance(node, ReturnStmt):
        return "Return" if node.value is None else "Return"
    if isinstance(node, IfStmt):
        return "If"
    if isinstance(node, IfBranch):
        return f"IfBranch({node.keyword})"
    if isinstance(node, WhileStmt):
        return "While"
    if isinstance(node, ForStmt):
        return f"For({node.var_name})"
    if isinstance(node, BreakStmt):
        return "Break"
    if isinstance(node, ContinueStmt):
        return "Continue"
    if isinstance(node, IntLit):
        return f"IntLit({node.value})"
    if isinstance(node, FloatLit):
        return f"FloatLit({node.value})"
    if isinstance(node, BoolLit):
        return f"BoolLit({node.value})"
    if isinstance(node, StrLit):
        return f"StrLit({node.value!r})"
    if isinstance(node, NullLit):
        return "NullLit"
    if isinstance(node, Ident):
        return f"Ident({node.name})"
    if isinstance(node, Unary):
        return f"Unary({node.op})"
    if isinstance(node, Binary):
        return f"Binary({node.op})"
    if isinstance(node, Call):
        return f"Call[{len(node.args)}]"
    if isinstance(node, Member):
        return f"Member(.{node.name})"
    if isinstance(node, Index):
        return "Index"
    if isinstance(node, ArrayLit):
        return f"ArrayLit[{len(node.elements)}]"
    return type(node).__name__


def ast_children(node: Node) -> list:
    """Child nodes of ``node`` in display order (lists are flattened)."""
    if isinstance(node, Program):
        return list(node.statements)
    if isinstance(node, ModuleDecl):
        return list(node.body)
    if isinstance(node, FuncDecl):
        kids: list = list(node.params)
        if node.return_type is not None:
            kids.append(node.return_type)
        kids.append(node.body)
        return kids
    if isinstance(node, Param):
        return [node.type] if node.type is not None else []
    if isinstance(node, VarDecl):
        kids = []
        if node.type is not None:
            kids.append(node.type)
        if node.value is not None:
            kids.append(node.value)
        return kids
    if isinstance(node, Assign):
        return [node.target, node.value]
    if isinstance(node, ExprStmt):
        return [node.expr]
    if isinstance(node, ReturnStmt):
        return [node.value] if node.value is not None else []
    if isinstance(node, IfStmt):
        kids = list(node.branches)
        if node.else_body is not None:
            kids.append(node.else_body)
        return kids
    if isinstance(node, IfBranch):
        return [node.condition, node.body]
    if isinstance(node, WhileStmt):
        return [node.condition, node.body]
    if isinstance(node, ForStmt):
        return [node.iterable, node.body]
    if isinstance(node, Unary):
        return [node.operand]
    if isinstance(node, Binary):
        return [node.left, node.right]
    if isinstance(node, Call):
        return [node.callee] + list(node.args)
    if isinstance(node, Member):
        return [node.obj]
    if isinstance(node, Index):
        return [node.obj, node.index]
    if isinstance(node, ArrayLit):
        return list(node.elements)
    return []


def ast_dump(node: Node) -> str:
    """Render an AST as an indented box-drawing tree."""
    lines = []

    def flatten(children):
        flat = []
        for child in children:
            if isinstance(child, list):
                flat.extend(child)
            else:
                flat.append(child)
        return flat

    def rec(n: Node, prefix: str, connector: str):
        lines.append(prefix + connector + ast_label(n))
        children = flatten(ast_children(n))
        child_prefix = prefix + ("    " if connector in ("└─ ", "") else "│   ")
        for i, child in enumerate(children):
            last = i == len(children) - 1
            rec(child, child_prefix, "└─ " if last else "├─ ")

    rec(node, "", "")
    return "\n".join(lines)
