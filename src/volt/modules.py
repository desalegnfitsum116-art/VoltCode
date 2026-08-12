"""Built-in hardware module registry for Volt.

Defines the ``<Module>.Init(...)`` pattern from the language spec: each module
is a :class:`volt.types.ModuleType` exposing an ``Init`` method (the static
initializer) plus instance methods and optional compile-time constants.
Calling ``Init`` returns the module's handle type, which is what gets stored
in variables and reused for later method calls.
"""

from .types import (
    INT,
    FLOAT,
    BOOL,
    VOID,
    Callable,
    Constant,
    ModuleType,
)


def _fn(name, param_defs, return_type):
    """Build a Callable from ``[(param_name, Type), ...]``."""
    return Callable(name, [(n, t) for n, t in param_defs], return_type)


# --------------------------------------------------------------------------- #
# Module definitions                                                          #
# --------------------------------------------------------------------------- #

# Arduino: board init. Handle has no instance methods in v1.
Arduino = ModuleType("Arduino", methods={}, constants={})

# Servo: PWM servo control on a digital pin (0-13 on AVR).
Servo = ModuleType(
    "Servo",
    methods={},
    constants={},
    pin_range=(0, 13),
)

# DigitalPin: digital GPIO. Modes mirror the Arduino constants.
DigitalPin = ModuleType(
    "DigitalPin",
    methods={},
    constants={
        "INPUT": Constant("INPUT", 0, INT),
        "OUTPUT": Constant("OUTPUT", 1, INT),
        "INPUT_PULLUP": Constant("INPUT_PULLUP", 2, INT),
    },
    pin_range=(0, 13),
    modes={0, 1, 2},
)

# AnalogPin: analog input (read) / PWM output (write).
AnalogPin = ModuleType(
    "AnalogPin",
    methods={},
    constants={},
    pin_range=(0, 5),
)

# Serial: the Arduino UART singleton. Used as a static namespace on the module
# name itself (e.g. Serial.begin(...), Serial.print(...)); no Init/handle needed.
Serial = ModuleType(
    "Serial",
    methods={},
    constants={},
)

# --- Methods (built after the ModuleType so Init can return the handle). --- #

Arduino.methods["Init"] = _fn("Init", [], Arduino)

Servo.methods["Init"] = _fn("Init", [("pin", INT)], Servo)
Servo.methods["write"] = _fn("write", [("angle", INT)], VOID)
Servo.methods["read"] = _fn("read", [], INT)
Servo.methods["attached"] = _fn("attached", [], BOOL)
Servo.methods["detach"] = _fn("detach", [], VOID)

DigitalPin.methods["Init"] = _fn("Init", [("pin", INT), ("mode", INT)], DigitalPin)
DigitalPin.methods["write"] = _fn("write", [("value", INT)], VOID)
DigitalPin.methods["read"] = _fn("read", [], INT)

AnalogPin.methods["Init"] = _fn("Init", [("pin", INT)], AnalogPin)
AnalogPin.methods["read"] = _fn("read", [], INT)
AnalogPin.methods["write"] = _fn("write", [("value", INT)], VOID)

# Serial: singleton UART module. begin/available/read/write take fixed shapes;
# print/println accept int, float, string, or bool (checked specially in sema).
Serial.methods["begin"] = _fn("begin", [("baud", INT)], VOID)
Serial.methods["print"] = _fn("print", [("value", INT)], VOID)
Serial.methods["println"] = _fn("println", [("value", INT)], VOID)
Serial.methods["write"] = _fn("write", [("byte", INT)], VOID)
Serial.methods["available"] = _fn("available", [], INT)
Serial.methods["read"] = _fn("read", [], INT)

# --------------------------------------------------------------------------- #
# Registries                                                                  #
# --------------------------------------------------------------------------- #

MODULES = {
    "Arduino": Arduino,
    "Servo": Servo,
    "DigitalPin": DigitalPin,
    "AnalogPin": AnalogPin,
    "Serial": Serial,
}

BUILTIN_FUNCTIONS = {
    "Delay": _fn("Delay", [("ms", INT)], VOID),
    "DelayMicroseconds": _fn("DelayMicroseconds", [("us", INT)], VOID),
    "Millis": _fn("Millis", [], INT),
    "Micros": _fn("Micros", [], INT),
}
