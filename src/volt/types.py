"""Type system for Volt.

Type objects are created by the semantic analyzer (:mod:`volt.sema`) and are
also attached to AST nodes (``node.inferred_type``) for use by code generation.

Primitive types are module-level singletons compared with ``is``. Composite
types (arrays, module/handle types) are compared structurally.
"""

from __future__ import annotations


class Type:
    """Base class for all Volt types."""

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"<{self}>"

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)


class PrimitiveType(Type):
    def __init__(self, name):
        self.name = name


# Singleton primitive types.
INT = PrimitiveType("int")
FLOAT = PrimitiveType("float")
BOOL = PrimitiveType("bool")
STRING = PrimitiveType("string")
VOID = PrimitiveType("void")
NULL = PrimitiveType("null")
ERROR = PrimitiveType("<error>")


def is_numeric(t: Type) -> bool:
    return t is INT or t is FLOAT


def is_truthy(t: Type) -> bool:
    """Types that are allowed in a condition / boolean-op position."""
    return (
        t is BOOL
        or t is INT
        or t is FLOAT
        or isinstance(t, ModuleType)
        or isinstance(t, ArrayType)
    )


class ArrayType(Type):
    """An array of ``element``, optionally with a fixed capacity ``size``."""

    def __init__(self, element: Type, size: int | None = None):
        self.element = element
        self.size = size
        self.name = f"{element}[]" if size is None else f"{element}[{size}]"

    def __eq__(self, other):
        return (
            isinstance(other, ArrayType)
            and other.element == self.element
            and other.size == self.size
        )

    def __hash__(self):
        return hash((self.name, self.size))


class Callable:
    """A callable signature: name, parameter (name, type) list, return type."""

    def __init__(self, name, params, return_type):
        self.name = name
        self.params = list(params)      # list[(param_name, Type)]
        self.return_type = return_type

    def __str__(self):
        return self.name


class Constant:
    """A compile-time module constant (e.g. DigitalPin.OUTPUT = 1)."""

    def __init__(self, name, value, type):
        self.name = name
        self.value = value
        self.type = type

    def __str__(self):
        return f"{self.name} = {self.value!r}"


class ModuleType(Type):
    """A hardware module AND the opaque handle type returned by its Init().

    Methods are ``name -> Callable``. Constants are ``name -> Constant``.
    ``pin_range`` (inclusive) and ``modes`` enable compile-time validation of
    Init() arguments when they are literal constants.
    """

    def __init__(self, name, methods=None, constants=None,
                 pin_range=None, modes=None):
        self.name = name
        self.methods = methods if methods is not None else {}
        self.constants = constants if constants is not None else {}
        self.pin_range = pin_range       # (min, max) or None
        self.modes = modes               # set of valid ints, or None

    @property
    def init_method(self) -> Callable:
        return self.methods["Init"]

    def __eq__(self, other):
        return isinstance(other, ModuleType) and other.name == self.name

    def __hash__(self):
        return hash(self.name)
