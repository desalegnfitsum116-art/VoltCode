"""Standalone tests for the Stage 5 AVR toolchain integration.

All tests are hermetic: they use temporary directories and `dry_run` mode so
no real avr-g++/avrdude installation is required.

Run with:  python tests/test_toolchain.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from volt import toolchain as tc  # noqa: E402
from volt.lexer import Lexer  # noqa: E402
from volt.parser import Parser  # noqa: E402
from volt.sema import Analyzer  # noqa: E402
from volt.codegen import CCodeGen  # noqa: E402

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


def gen_cpp(src):
    tree = Parser(Lexer(src).tokenize()).parse_program()
    analyzer = Analyzer("<test>")
    ok = analyzer.analyze(tree)
    if not ok:
        raise AssertionError("analysis failed: " + "; ".join(str(d) for d in analyzer.diagnostics))
    return CCodeGen("<test>").generate(tree)


def make_env(root):
    """Fabricate a plausible (but file-less) toolchain layout."""
    core = os.path.join(root, "hw", "avr", "1.8.6", "cores", "arduino")
    variant = os.path.join(root, "hw", "avr", "1.8.6", "variants", "standard")
    gcc_dir = os.path.join(root, "tools")
    avr_dir = os.path.join(root, "avrdude")
    lib = os.path.join(root, "lib")
    for d in (core, variant, gcc_dir, avr_dir, lib):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(core, "wiring.cpp"), "w", encoding="utf-8") as fh:
        fh.write("int dummy;")
    with open(os.path.join(core, "main.cpp"), "w", encoding="utf-8") as fh:
        fh.write("int main() { return 0; }")
    with open(os.path.join(variant, "pins_arduino.h"), "w", encoding="utf-8") as fh:
        fh.write("// pins")
    with open(os.path.join(lib, "Servo.h"), "w", encoding="utf-8") as fh:
        fh.write("class Servo {};")
    cfg = tc.ToolchainConfig(
        board="uno", port="COM3",
        gcc_dir=gcc_dir, avrdude_dir=avr_dir,
        avrdude_conf=os.path.join(avr_dir, "avrdude.conf"),
        core=core, variant=variant, libraries=[lib],
    )
    return tc.finalize(cfg)


def test_board_table():
    print("== board table ==")
    check("uno spec", tc.BOARDS["uno"].mcu == "atmega328p" and tc.BOARDS["uno"].baud == 115200)
    check("nano baud", tc.BOARDS["nano"].baud == 57600)
    check("mega mcu", tc.BOARDS["mega"].mcu == "atmega2560" and tc.BOARDS["mega"].variant == "mega")


def test_build_dry_run():
    print("== build dry-run commands ==")
    tmp = tempfile.mkdtemp()
    cfg = make_env(tmp)
    code = gen_cpp("var led = DigitalPin.Init(13, DigitalPin.OUTPUT)\nwhile true:\n    led.write(1)\n")
    hex_path, commands = tc.build_sketch(cfg, code, "blink",
                                         os.path.join(tmp, "build"), dry_run=True, echo=False)
    all_text = "\n".join(" ".join(cmd) for cmd in commands)

    check("sketch written", os.path.isfile(os.path.join(tmp, "build", "blink", "blink.cpp")))
    with open(os.path.join(tmp, "build", "blink", "blink.cpp"), encoding="utf-8") as fh:
        sketch = fh.read()
    check("sketch is C++ output", "void setup()" in sketch and "void loop()" in sketch)
    check("mcu flag", "-mmcu=atmega328p" in all_text)
    check("f_cpu flag", "-DF_CPU=16000000L" in all_text)
    check("board macro", "-DARDUINO_AVR_UNO" in all_text)
    check("core include", "-I" + cfg.core in all_text)
    check("variant include", "-I" + cfg.variant in all_text)
    check("library include", "-I" + os.path.join(tmp, "lib") in all_text)
    check("sketch object", os.path.join("obj", "blink.cpp.o") in all_text)
    check("core object compiled", os.path.join("obj", "wiring.cpp.o") in all_text)
    check("link elf", "-o " + os.path.join(tmp, "build", "blink", "blink.elf") in all_text)
    check("objcopy hex", ("-O" in all_text and "ihex" in all_text and
                          os.path.join(tmp, "build", "blink", "blink.hex") in all_text))
    check("hex path returned", hex_path.endswith(os.path.join("blink", "blink.hex")))


def test_upload_command():
    print("== upload command ==")
    tmp = tempfile.mkdtemp()
    cfg = make_env(tmp)
    hexf = os.path.join(tmp, "blink.hex")
    cmd = tc.upload_command(cfg, hexf)
    text = " ".join(cmd)
    check("avrdude invoked", cmd[0].endswith("avrdude") or "avrdude" in cmd[0])
    check("avrdude conf", "-C" in cmd and "avrdude.conf" in text)
    check("part", "-p atmega328p" in text)
    check("programmer", "-c arduino" in text)
    check("port", "-P COM3" in text)
    check("baud", "-b 115200" in text)
    check("hex payload", f"-U flash:w:{hexf}:i" in text)


def test_upload_validation():
    print("== upload validation ==")
    tmp = tempfile.mkdtemp()
    cfg = make_env(tmp)
    cfg.port = None
    try:
        tc.upload_hex(cfg, os.path.join(tmp, "x.hex"), dry_run=False)
        check("missing port raises", False)
    except tc.ToolchainError as exc:
        check("missing port raises", "upload port" in str(exc))


def test_missing_tool_error():
    print("== missing tool error ==")
    tmp = tempfile.mkdtemp()
    cfg = make_env(tmp)
    cfg.gcc_dir = os.path.join(tmp, "empty")
    os.makedirs(cfg.gcc_dir, exist_ok=True)
    tc.finalize(cfg)  # resolves paths, but avr-g++ is not present
    try:
        tc.build_sketch(cfg, "// hi\n", "x", tmp, dry_run=False)
        check("missing avr-g++ raises", False)
    except tc.ToolchainError as exc:
        msg = str(exc)
        check("missing avr-g++ raises", "avr-g++" in msg)


def test_config_and_overrides():
    print("== config file + overrides ==")
    tmp = tempfile.mkdtemp()
    cfg_file = os.path.join(tmp, "voltc.json")
    with open(cfg_file, "w", encoding="utf-8") as fh:
        fh.write('{"board": "uno", "port": "COM9", '
                 '"toolchain": {"gcc_dir": "%s", "core": "%s"}}\n'
                 % (os.path.join(tmp, "tools").replace("\\", "/"),
                    os.path.join(tmp, "hw", "avr", "1.8.6", "cores", "arduino").replace("\\", "/")))
    cfg = tc.resolve_toolchain(cfg_file)
    check("board from file", cfg.board == "uno")
    check("port from file", cfg.port == "COM9")
    check("gcc_dir from file", cfg.gcc_dir == os.path.join(tmp, "tools"))
    check("core from file", cfg.core == os.path.join(tmp, "hw", "avr", "1.8.6", "cores", "arduino"))

    cfg2 = tc.resolve_toolchain(cfg_file, {"board": "mega"})
    check("CLI override wins", cfg2.board == "mega")
    check("port still from file", cfg2.port == "COM9")

    try:
        tc.resolve_toolchain(cfg_file, {"board": "banana"})
        check("unknown board raises", False)
    except tc.ToolchainError as exc:
        check("unknown board raises", "unknown board 'banana'" in str(exc))


def test_default_variant():
    print("== default variant derivation ==")
    tmp = tempfile.mkdtemp()
    core = os.path.join(tmp, "hw", "avr", "1.8.6", "cores", "arduino")
    os.makedirs(core, exist_ok=True)
    cfg = tc.ToolchainConfig(board="uno", core=core)
    check("standard variant", tc.default_variant(cfg) ==
          os.path.join(tmp, "hw", "avr", "1.8.6", "variants", "standard"))
    cfg2 = tc.ToolchainConfig(board="mega", core=core)
    check("mega variant", tc.default_variant(cfg2) ==
          os.path.join(tmp, "hw", "avr", "1.8.6", "variants", "mega"))


def test_dry_run_requires_config():
    print("== dry-run config guard ==")
    tmp = tempfile.mkdtemp()
    cfg = tc.ToolchainConfig(board="uno")
    try:
        tc.build_sketch(cfg, "// hi\n", "x", tmp, dry_run=True, echo=False)
        check("dry-run without toolchain raises", False)
    except tc.ToolchainError as exc:
        check("dry-run without toolchain raises", "no toolchain configured" in str(exc))
    try:
        tc.upload_hex(cfg, os.path.join(tmp, "x.hex"), dry_run=True, echo=False)
        check("dry-run upload without port raises", False)
    except tc.ToolchainError as exc:
        check("dry-run upload without port raises", "upload port" in str(exc))


def test_missing_config_file():
    print("== missing config file ==")
    try:
        tc.resolve_toolchain(os.path.join(tempfile.mkdtemp(), "nope.json"))
        check("missing config raises", False)
    except tc.ToolchainError as exc:
        check("missing config raises", "config file not found" in str(exc))


def main():
    test_board_table()
    test_build_dry_run()
    test_upload_command()
    test_upload_validation()
    test_missing_tool_error()
    test_config_and_overrides()
    test_default_variant()
    test_dry_run_requires_config()
    test_missing_config_file()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
