#!/usr/bin/env python3
"""voltc — the Volt compiler command-line front-end.

Stages 1-4 provide `lex`, `parse`, `check`, and `codegen` (tokenize, AST,
semantic analysis, Volt -> Arduino C++). Stage 5 adds the AVR toolchain:
`build` compiles a sketch to an Intel HEX binary with avr-g++, and `upload`
flashes it with avrdude. `toolchain` prints the resolved toolchain.

Usage:
    python voltc.py lex <file.volt>
    python voltc.py parse <file.volt>
    python voltc.py check <file.volt>
    python voltc.py codegen <file.volt>
    python voltc.py build <file.volt> [-o DIR] [--board NAME] [--dry-run]
    python voltc.py upload <file.volt> [-p PORT] [--board NAME] [--dry-run]
    python voltc.py monitor [-p PORT] [--baud N] [--list-ports]
    python voltc.py toolchain
    python voltc.py demo
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, "src")

from volt.lexer import LexError, Lexer  # noqa: E402
from volt.parser import ParseError, Parser  # noqa: E402
from volt.ast import ast_dump  # noqa: E402
from volt.sema import Analyzer  # noqa: E402
from volt.codegen import CodegenError, CCodeGen  # noqa: E402
from volt import toolchain as tc  # noqa: E402
from volt import monitor as mon  # noqa: E402


def _load(filename):
    """Read and parse a .volt file. Returns (tree, error_str) or (None, msg)."""
    source = _read_source(filename)
    if source is None:
        return None, "cannot read source"
    try:
        tokens = Lexer(source, filename).tokenize()
        tree = Parser(tokens, filename).parse_program()
    except (LexError, ParseError) as exc:
        return None, str(exc)
    return tree, None


def _read_source(filename):
    try:
        with open(filename, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        print(f"error: cannot read {filename}: {exc}", file=sys.stderr)
        return None


def _display(text):
    """Escape control characters so token dumps stay single-line."""
    return (
        text.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def print_token_stream(tokens):
    """Render a token list in a readable column-aligned table."""
    headers = ("#", "LINE:COL", "KIND", "LEXEME", "VALUE")
    widths = (4, 9, 12, 20, 24)
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)

    print(fmt.format(*headers))
    print("  " + "-" * (sum(widths) + 2 * (len(widths) - 1)))
    for i, tok in enumerate(tokens):
        value = "" if tok.value is None else repr(tok.value)
        print(fmt.format(str(i), f"{tok.line}:{tok.col}", tok.kind.name, _display(tok.lexeme), _display(value)))


def cmd_lex(filename):
    source = _read_source(filename)
    if source is None:
        return 1

    try:
        tokens = Lexer(source, filename).tokenize()
    except LexError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print_token_stream(tokens)
    return 0


def cmd_parse(filename):
    tree, err = _load(filename)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(ast_dump(tree))
    return 0


def cmd_check(filename):
    tree, err = _load(filename)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    analyzer = Analyzer(filename)
    ok = analyzer.analyze(tree)
    for diag in analyzer.diagnostics:
        print(diag)
    if ok:
        print(f"{filename}: analysis succeeded (no errors)")
    return 0 if ok else 1


def _analyzed_code(filename):
    """Run lex -> parse -> analyze -> codegen. Returns (source, err) or (None, msg)."""
    tree, err = _load(filename)
    if err:
        return None, err
    analyzer = Analyzer(filename)
    if not analyzer.analyze(tree):
        return None, "\n".join(str(d) for d in analyzer.diagnostics)
    try:
        return CCodeGen(filename).generate(tree), None
    except CodegenError as exc:
        return None, str(exc)


def cmd_codegen(filename):
    code, err = _analyzed_code(filename)
    if err:
        print(err, file=sys.stderr)
        return 1
    print(code)
    return 0


_BOOL_FLAGS = ("--dry-run",)
_VALUE_FLAGS = ("-o", "-p", "--port", "--board", "--config", "--gcc-dir",
                "--core", "--variant", "--avrdude-dir", "--avrdude-conf",
                "--libraries")


def _parse_opts(args, value_flags=_VALUE_FLAGS, bool_flags=_BOOL_FLAGS):
    """Parse `--flag value` / `-o value` options plus positional args."""
    positional = []
    opts = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in bool_flags:
            opts[arg] = True
            i += 1
        elif arg in value_flags:
            if i + 1 >= len(args):
                raise SystemExit(f"missing value for {arg}")
            opts[arg] = args[i + 1]
            i += 2
        else:
            positional.append(arg)
            i += 1
    return positional, opts


def _resolve_config(opts):
    mapping = {
        "--board": "board",
        "-p": "port",
        "--port": "port",
        "--gcc-dir": "gcc_dir",
        "--core": "core",
        "--variant": "variant",
        "--avrdude-dir": "avrdude_dir",
        "--avrdude-conf": "avrdude_conf",
    }
    overrides = {attr: opts[flag] for flag, attr in mapping.items() if flag in opts}
    if "--libraries" in opts:
        overrides["libraries"] = [p for p in opts["--libraries"].split(";") if p]
    config_file = opts.get("--config")
    if config_file is None:
        default = os.path.join(os.getcwd(), "voltc.json")
        config_file = default if os.path.isfile(default) else None
    return tc.resolve_toolchain(config_file, overrides)


def _usage_build():
    return ("usage: python voltc.py build <file.volt> [-o DIR] "
            "[--board NAME] [--config FILE] [--dry-run]")


def _usage_upload():
    return ("usage: python voltc.py upload <file.volt> [-p PORT] "
            "[--board NAME] [--config FILE] [--dry-run]")


def cmd_build(argv):
    pos, opts = _parse_opts(argv)
    if len(pos) != 1:
        print(_usage_build(), file=sys.stderr)
        return 2
    code, err = _analyzed_code(pos[0])
    if err:
        print(err, file=sys.stderr)
        return 1
    try:
        cfg = _resolve_config(opts)
    except tc.ToolchainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    name = os.path.splitext(os.path.basename(pos[0]))[0]
    out_dir = opts.get("-o") or "build"
    dry_run = bool(opts.get("--dry-run"))
    try:
        hex_path, _commands = tc.build_sketch(cfg, code, name, out_dir, dry_run=dry_run)
    except tc.ToolchainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not dry_run:
        print(f"built {hex_path}")
    return 0


def cmd_upload(argv):
    pos, opts = _parse_opts(argv)
    if len(pos) != 1:
        print(_usage_upload(), file=sys.stderr)
        return 2
    code, err = _analyzed_code(pos[0])
    if err:
        print(err, file=sys.stderr)
        return 1
    try:
        cfg = _resolve_config(opts)
    except tc.ToolchainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    name = os.path.splitext(os.path.basename(pos[0]))[0]
    out_dir = opts.get("-o") or "build"
    dry_run = bool(opts.get("--dry-run"))
    try:
        hex_path, _commands = tc.build_sketch(cfg, code, name, out_dir, dry_run=dry_run)
        tc.upload_hex(cfg, hex_path, dry_run=dry_run)
    except tc.ToolchainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not dry_run:
        print(f"uploaded {hex_path} to {cfg.port}")
    return 0


def cmd_toolchain(argv):
    pos, opts = _parse_opts(argv, value_flags=("--config",))
    try:
        cfg = _resolve_config(opts)
    except tc.ToolchainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(tc.describe(cfg))
    return 0


def cmd_monitor(argv):
    pos, opts = _parse_opts(argv, value_flags=("-p", "--port", "--baud"),
                            bool_flags=("--list-ports",))
    if opts.get("--list-ports"):
        try:
            ports = mon.list_ports()
        except mon.MonitorError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not ports:
            print("no serial ports found")
        else:
            for p in ports:
                print(p)
        return 0
    port = opts.get("-p") or opts.get("--port")
    if port is None:
        print("usage: python voltc.py monitor [-p PORT] [--baud N] [--list-ports]",
              file=sys.stderr)
        return 2
    baud = int(opts.get("--baud") or 9600)
    try:
        ser = mon.open_serial(port, baud)
    except mon.MonitorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"connected to {port} at {baud} baud (Ctrl+C to exit)")
    try:
        stop = mon.stop_flag()
        mon.monitor_loop(ser, stop=stop)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_demo():
    """Lex, parse, and analyze a few representative snippets."""
    snippets = {
        "hardware init pattern": (
            "var myBoard = Arduino.Init()\n"
            "var myServo = Servo.Init(5)\n"
            "myServo.write(90)\n"
        ),
        "control flow + comments": (
            "// sweep a servo\n"
            "var myServo = Servo.Init(9)\n"
            "var pos int = 0\n"
            "while pos <= 180:\n"
            "    myServo.write(pos)  // move it\n"
            "    pos = pos + 1\n"
            "    if pos == 90:\n"
            "        break\n"
        ),
        "function definition": (
            "func add(a int, b int) -> int:\n"
            "    return a + b\n"
            "\n"
            "var x = add(1, 2)\n"
        ),
        "literals & operators": (
            "var a = 1\n"
            "var b = 2\n"
            "var c = true\n"
            'var s = "hi\\n"\n'
            "var f = 3.14\n"
            "var flag = a >= b and not c\n"
        ),
    }

    for name, src in snippets.items():
        print("=" * 78)
        print(f"=== {name}")
        print("=" * 78)
        print("--- source ---")
        for lineno, text in enumerate(src.splitlines(), 1):
            print(f"{lineno:>3} | {text}")
        try:
            tree = Parser(Lexer(src, "<demo>").tokenize(), "<demo>").parse_program()
        except (LexError, ParseError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("--- AST ---")
        print(ast_dump(tree))
        print("--- analysis ---")
        analyzer = Analyzer("<demo>")
        ok = analyzer.analyze(tree)
        for diag in analyzer.diagnostics:
            print(diag)
        print("OK" if ok else "FAILED")
        print()
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 0
    if argv[1] == "lex":
        if len(argv) != 3:
            print("usage: python voltc.py lex <file.volt>", file=sys.stderr)
            return 2
        return cmd_lex(argv[2])
    if argv[1] == "parse":
        if len(argv) != 3:
            print("usage: python voltc.py parse <file.volt>", file=sys.stderr)
            return 2
        return cmd_parse(argv[2])
    if argv[1] == "check":
        if len(argv) != 3:
            print("usage: python voltc.py check <file.volt>", file=sys.stderr)
            return 2
        return cmd_check(argv[2])
    if argv[1] == "codegen":
        if len(argv) != 3:
            print("usage: python voltc.py codegen <file.volt>", file=sys.stderr)
            return 2
        return cmd_codegen(argv[2])
    if argv[1] == "build":
        return cmd_build(argv[2:])
    if argv[1] == "upload":
        return cmd_upload(argv[2:])
    if argv[1] == "monitor":
        return cmd_monitor(argv[2:])
    if argv[1] == "toolchain":
        return cmd_toolchain(argv[2:])
    if argv[1] == "demo":
        return cmd_demo()
    print(f"unknown subcommand: {argv[1]}", file=sys.stderr)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
