"""Stage 5: AVR toolchain integration (avr-g++ + avrdude).

Turns the C++ emitted by the Stage 4 code generator into a flashing-ready
Intel HEX binary and uploads it to a board.

The generator emits Arduino C++ (a sketch), which only compiles when the
Arduino AVR core is available (Arduino.h, wiring_*.c, main.cpp, variants and
optional libraries such as Servo). This module locates that core and the AVR
toolchain, compiles the sketch together with the core sources, links, and
converts the result with ``avr-objcopy``.

Toolchain resolution order (first hit wins):
  1. Explicit tool paths passed on the command line.
  2. A ``voltc.json`` config file in the project root (or ``--config``).
  3. Auto-detection of an Arduino IDE 1.x or Arduino15/CLI install.

When a real build cannot locate every piece it prints a helpful message that
also serves as the ``voltc.json`` template. ``--dry-run`` prints the exact
commands that would be executed without running anything, which keeps the
whole stage testable without a toolchain.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field


class ToolchainError(Exception):
    """Raised when the AVR toolchain cannot be located or a command fails."""


@dataclass(frozen=True)
class BoardSpec:
    mcu: str
    f_cpu: str
    baud: int
    variant: str
    macro: str


BOARDS = {
    "uno": BoardSpec("atmega328p", "16000000L", 115200, "standard", "ARDUINO_AVR_UNO"),
    "nano": BoardSpec("atmega328p", "16000000L", 57600, "standard", "ARDUINO_AVR_NANO"),
    "mega": BoardSpec("atmega2560", "16000000L", 115200, "mega", "ARDUINO_AVR_MEGA"),
}


@dataclass
class ToolchainConfig:
    board: str = "uno"
    port: str | None = None
    gcc_dir: str | None = None      # dir containing avr-g++/avr-gcc/avr-objcopy/avr-size
    avrdude_dir: str | None = None  # dir containing avrdude
    avrdude_conf: str | None = None # avrdude.conf (default: <avrdude_dir>/../etc/avrdude.conf)
    core: str | None = None         # .../cores/arduino
    variant: str | None = None      # .../variants/<variant> (pins_arduino.h)
    libraries: list = field(default_factory=list)

    # Resolved tool paths (filled by finalize()).
    gcc: str | None = None
    gxx: str | None = None
    objcopy: str | None = None
    size: str | None = None
    avrdude: str | None = None


# --------------------------------------------------------------------------- #
# Path helpers                                                                 #
# --------------------------------------------------------------------------- #

def _suffix():
    return ".exe" if os.name == "nt" else ""


def _tool_path(directory, name):
    """Path of ``name`` inside ``directory``, preferring the OS exe suffix."""
    with_suffix = os.path.join(directory, name + _suffix())
    without_suffix = os.path.join(directory, name)
    if os.path.isfile(with_suffix):
        return with_suffix
    if os.path.isfile(without_suffix):
        return without_suffix
    return with_suffix


def _find_newest_dir(root, tail_parts):
    """Deepest/newest directory under ``root`` ending with ``tail_parts``."""
    tail = os.path.normcase(os.path.join(*tail_parts))
    hits = []
    if os.path.isdir(root):
        for dirpath, _dirnames, _filenames in os.walk(root):
            if os.path.normcase(dirpath).endswith(tail):
                hits.append(dirpath)
    return max(hits) if hits else None


def _find_newest_file(root, rel_parts):
    """Newest occurrence of the relative path ``rel_parts`` under ``root``."""
    rel = os.path.normcase(os.path.join(*rel_parts))
    hits = []
    if os.path.isdir(root):
        for dirpath, _dirnames, _filenames in os.walk(root):
            candidate = os.path.normcase(os.path.join(dirpath, rel))
            if os.path.isfile(candidate):
                hits.append(candidate)
    return max(hits) if hits else None


# --------------------------------------------------------------------------- #
# Configuration                                                                #
# --------------------------------------------------------------------------- #

def load_config_file(path):
    """Parse a ``voltc.json`` file into a dict."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolchainError(f"cannot read config {path}: {exc}")


def finalize(cfg: ToolchainConfig) -> ToolchainConfig:
    """Derive per-tool paths from the configured directories."""
    if cfg.gcc_dir:
        cfg.gcc = _tool_path(cfg.gcc_dir, "avr-gcc")
        cfg.gxx = _tool_path(cfg.gcc_dir, "avr-g++")
        cfg.objcopy = _tool_path(cfg.gcc_dir, "avr-objcopy")
        cfg.size = _tool_path(cfg.gcc_dir, "avr-size")
    if cfg.avrdude_dir:
        cfg.avrdude = _tool_path(cfg.avrdude_dir, "avrdude")
        if not cfg.avrdude_conf:
            cfg.avrdude_conf = os.path.normpath(
                os.path.join(cfg.avrdude_dir, "..", "etc", "avrdude.conf")
            )
    return cfg


def _native(path):
    """Normalize forward slashes in config values to the OS separator."""
    if path is None:
        return None
    return path.replace("/", os.sep)


def default_variant(cfg: ToolchainConfig) -> str | None:
    """Standard variant dir for the configured core + board, if any."""
    if not cfg.core or cfg.board not in BOARDS:
        return None
    avr_root = os.path.dirname(os.path.dirname(cfg.core))
    return os.path.join(avr_root, "variants", BOARDS[cfg.board].variant)


def resolve_toolchain(config_file=None, overrides=None) -> ToolchainConfig:
    """Merge CLI overrides > config file > auto-detection into a config."""
    overrides = overrides or {}
    data = {}
    if config_file:
        if not os.path.isfile(config_file):
            raise ToolchainError(f"config file not found: {config_file}")
        data = load_config_file(config_file)

    cfg = ToolchainConfig(board=data.get("board") or "uno", port=data.get("port"))
    tc = data.get("toolchain") or {}
    cfg.gcc_dir = _native(tc.get("gcc_dir"))
    cfg.avrdude_dir = _native(tc.get("avrdude_dir"))
    cfg.avrdude_conf = _native(tc.get("avrdude_conf"))
    cfg.core = _native(tc.get("core"))
    cfg.variant = _native(tc.get("variant"))
    cfg.libraries = [_native(p) for p in (tc.get("libraries") or [])]

    for key, value in overrides.items():
        if value is not None:
            setattr(cfg, key, value)

    if cfg.board not in BOARDS:
        raise ToolchainError(
            f"unknown board '{cfg.board}' (known boards: {', '.join(sorted(BOARDS))})"
        )

    if not (cfg.gcc_dir and cfg.core):
        auto = autodetect()
        if auto is not None:
            if not cfg.gcc_dir:
                cfg.gcc_dir = auto.gcc_dir
            if not cfg.core:
                cfg.core = auto.core
            if not cfg.variant:
                cfg.variant = auto.variant
            if not cfg.avrdude_dir:
                cfg.avrdude_dir = auto.avrdude_dir
            if not cfg.avrdude_conf:
                cfg.avrdude_conf = auto.avrdude_conf

    if not cfg.variant:
        cfg.variant = default_variant(cfg)

    if not cfg.libraries and cfg.core:
        servo = os.path.join(os.path.dirname(os.path.dirname(cfg.core)),
                             "libraries", "Servo", "src")
        if os.path.isdir(servo):
            cfg.libraries = [servo]

    return finalize(cfg)


def _validate_dry_run(cfg: ToolchainConfig, need_upload=False):
    """Dry-run still needs every *value* needed to build command lines."""
    missing = []
    if not cfg.gcc_dir:
        missing.append("avr-g++ toolchain directory (gcc_dir)")
    if not cfg.core:
        missing.append("Arduino core directory (core)")
    if need_upload:
        if not cfg.avrdude_dir:
            missing.append("avrdude directory (avrdude_dir)")
        if not cfg.avrdude_conf:
            missing.append("avrdude.conf")
        if not cfg.port:
            missing.append("upload port (-p/--port)")
    if missing:
        raise ToolchainError(
            "no toolchain configured; missing: " + ", ".join(missing) + ".\n"
            "Run `python voltc.py toolchain` to see the detected layout, or create a "
            "voltc.json in the project root."
        )


def validate(cfg: ToolchainConfig, need_upload=False):
    """Raise ToolchainError listing every missing piece for a real build."""
    missing = []
    if cfg.board not in BOARDS:
        raise ToolchainError(
            f"unknown board '{cfg.board}' (known boards: {', '.join(sorted(BOARDS))})"
        )
    for label, path in (("avr-g++", cfg.gxx), ("avr-gcc", cfg.gcc),
                        ("avr-objcopy", cfg.objcopy), ("avr-size", cfg.size)):
        if not path or not os.path.isfile(path):
            missing.append(label)
    if not cfg.core or not os.path.isdir(cfg.core):
        missing.append("Arduino core directory")
    if not cfg.variant or not os.path.isdir(cfg.variant):
        missing.append("Arduino variant directory (pins_arduino.h)")
    if need_upload:
        if not cfg.avrdude or not os.path.isfile(cfg.avrdude):
            missing.append("avrdude")
        if not cfg.avrdude_conf or not os.path.isfile(cfg.avrdude_conf):
            missing.append("avrdude.conf")
        if not cfg.port:
            missing.append("upload port (-p/--port)")
    if missing:
        raise ToolchainError(_missing_message(missing))


def _missing_message(missing):
    return "\n".join([
        "AVR toolchain is incomplete; missing: " + ", ".join(missing) + ".",
        "Install Arduino IDE 1.x or Arduino CLI, or create a voltc.json in the",
        "project root pointing at your toolchain, e.g.:",
        "  {",
        '    "board": "uno",',
        '    "port": "COM3",',
        '    "toolchain": {',
        '      "gcc_dir": "C:/path/to/avr/bin",',
        '      "avrdude_dir": "C:/path/to/avrdude/bin",',
        '      "core": "C:/path/to/cores/arduino",',
        '      "variant": "C:/path/to/variants/standard"',
        "    }",
        "  }",
    ])


# --------------------------------------------------------------------------- #
# Auto-detection                                                               #
# --------------------------------------------------------------------------- #

def autodetect():
    """Return a best-effort ToolchainConfig or None if nothing is installed."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    cfg = _detect_arduino15(os.path.join(local_app_data, "Arduino15", "packages", "arduino"))
    if cfg is not None:
        return cfg
    for ide in (r"C:\Program Files (x86)\Arduino", r"C:\Program Files\Arduino"):
        cfg = _detect_ide(ide)
        if cfg is not None:
            return cfg
    return None


def _detect_arduino15(base):
    """Arduino CLI / IDE 2.x package layout:
    packages/arduino/hardware/avr/<ver>/cores/arduino
    packages/arduino/tools/avr-gcc/<ver>/bin/avr-g++
    packages/arduino/tools/avrdude/<ver>/bin|etc
    """
    core = _find_newest_dir(os.path.join(base, "hardware", "avr"), ("cores", "arduino"))
    if core is None:
        return None
    cfg = ToolchainConfig()
    cfg.core = core
    gxx = _find_newest_file(os.path.join(base, "tools", "avr-gcc"),
                            ("bin", "avr-g++" + _suffix()))
    if gxx:
        cfg.gcc_dir = os.path.dirname(gxx)
    avrdude = _find_newest_file(os.path.join(base, "tools", "avrdude"),
                                ("bin", "avrdude" + _suffix()))
    if avrdude:
        cfg.avrdude_dir = os.path.dirname(avrdude)
        conf = _find_newest_file(os.path.join(base, "tools", "avrdude"),
                                 ("etc", "avrdude.conf"))
        if conf:
            cfg.avrdude_conf = conf
    return finalize(cfg)


def _detect_ide(ide):
    """Arduino IDE 1.x layout:
    <ide>/hardware/arduino/avr/cores/arduino
    <ide>/hardware/tools/avr/bin
    <ide>/hardware/tools/avr/etc/avrdude.conf
    """
    core = os.path.join(ide, "hardware", "arduino", "avr", "cores", "arduino")
    if not os.path.isdir(core):
        return None
    cfg = ToolchainConfig()
    cfg.core = core
    tools = os.path.join(ide, "hardware", "tools", "avr")
    if os.path.isdir(os.path.join(tools, "bin")):
        cfg.gcc_dir = os.path.join(tools, "bin")
        cfg.avrdude_dir = os.path.join(tools, "bin")
    conf = os.path.join(tools, "etc", "avrdude.conf")
    if os.path.isfile(conf):
        cfg.avrdude_conf = conf
    return finalize(cfg)


# --------------------------------------------------------------------------- #
# Build / upload                                                               #
# --------------------------------------------------------------------------- #

def core_sources(core_dir):
    """The .c/.cpp sources compiled by the Arduino core."""
    sources = []
    if os.path.isdir(core_dir):
        for name in sorted(os.listdir(core_dir)):
            if name.endswith(".c") or name.endswith(".cpp"):
                sources.append(os.path.join(core_dir, name))
    return sources


def library_sources(libraries):
    """Every .c/.cpp source found recursively under the library directories."""
    sources = []
    for lib in libraries or []:
        if not os.path.isdir(lib):
            continue
        for dirpath, _dirnames, filenames in os.walk(lib):
            for name in sorted(filenames):
                if name.endswith(".c") or name.endswith(".cpp"):
                    sources.append(os.path.join(dirpath, name))
    return sources


def _assign_obj_names(sources):
    """Per-source object names; duplicate basenames get a numeric suffix."""
    from collections import Counter
    base_counts = Counter(os.path.basename(s) for s in sources)
    seen = {}
    names = []
    for s in sources:
        base = os.path.basename(s)
        if base_counts[base] == 1:
            names.append(base + ".o")
        else:
            n = seen.get(base, 0)
            seen[base] = n + 1
            names.append(f"{base}.{n}.o")
    return names


def _compile_command(cfg, bs, src, obj):
    include_dirs = ["-I" + cfg.core, "-I" + cfg.variant]
    include_dirs += ["-I" + lib for lib in cfg.libraries]
    common = [f"-mmcu={bs.mcu}", f"-DF_CPU={bs.f_cpu}",
              "-D" + bs.macro, "-DARDUINO_ARCH_AVR"] + include_dirs
    if src.endswith(".cpp"):
        flags = ["-c", "-g", "-Os", "-w", "-std=gnu++11", "-fpermissive",
                 "-fno-exceptions", "-ffunction-sections", "-fdata-sections",
                 "-fno-threadsafe-statics", "-MMD"]
        tool = cfg.gxx
    else:
        flags = ["-c", "-g", "-Os", "-w", "-std=gnu11",
                 "-ffunction-sections", "-fdata-sections", "-MMD"]
        tool = cfg.gcc
    return [tool] + flags + common + [src, "-o", obj]


def _quote(arg):
    if " " in arg:
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def _format_command(cmd):
    return " ".join(_quote(c) for c in cmd)


def _run(cmd):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        raise ToolchainError(f"cannot run {cmd[0]}: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ToolchainError(f"command failed:\n  {_format_command(cmd)}\n{detail}")


def build_sketch(cfg: ToolchainConfig, source: str, name: str, out_dir: str,
                 dry_run=False, echo=True):
    """Write the sketch source and compile it to a .hex file.

    Returns ``(hex_path, commands)``. In ``dry_run`` mode the commands are
    printed (when ``echo``) but never executed and no tool checks are made.
    """
    if dry_run:
        _validate_dry_run(cfg, need_upload=False)
    else:
        validate(cfg, need_upload=False)

    bs = BOARDS[cfg.board]
    sketch_dir = os.path.join(out_dir, name)
    obj_dir = os.path.join(sketch_dir, "obj")
    os.makedirs(obj_dir, exist_ok=True)

    cpp_path = os.path.join(sketch_dir, name + ".cpp")
    with open(cpp_path, "w", encoding="utf-8") as fh:
        fh.write(source)

    elf_path = os.path.join(sketch_dir, name + ".elf")
    hex_path = os.path.join(sketch_dir, name + ".hex")

    sources = [cpp_path] + core_sources(cfg.core) + library_sources(cfg.libraries)
    objs = [os.path.join(obj_dir, n) for n in _assign_obj_names(sources)]

    compile_cmds = [_compile_command(cfg, bs, src, obj)
                    for src, obj in zip(sources, objs)]
    link_cmd = ([cfg.gcc, "-Os", "-Wl,--gc-sections", f"-mmcu={bs.mcu}",
                 "-o", elf_path] + objs)
    copy_cmd = [cfg.objcopy, "-O", "ihex", "-R", ".eeprom", elf_path, hex_path]
    size_cmd = [cfg.size, elf_path]

    commands = compile_cmds + [link_cmd, copy_cmd, size_cmd]
    for cmd in commands:
        if dry_run:
            if echo:
                print("+ " + _format_command(cmd))
        else:
            _run(cmd)
    return hex_path, commands


def upload_command(cfg: ToolchainConfig, hex_path: str):
    bs = BOARDS[cfg.board]
    return [cfg.avrdude, "-C", cfg.avrdude_conf, "-v", "-p", bs.mcu,
            "-c", "arduino", "-P", cfg.port, "-b", str(bs.baud),
            "-U", f"flash:w:{hex_path}:i"]


def upload_hex(cfg: ToolchainConfig, hex_path: str, dry_run=False, echo=True):
    """Flash ``hex_path`` with avrdude. Returns the command."""
    if dry_run:
        _validate_dry_run(cfg, need_upload=True)
    else:
        validate(cfg, need_upload=True)
    cmd = upload_command(cfg, hex_path)
    if dry_run:
        if echo:
            print("+ " + _format_command(cmd))
    else:
        _run(cmd)
    return cmd


def describe(cfg: ToolchainConfig) -> str:
    bs = BOARDS[cfg.board]
    libs = ", ".join(cfg.libraries) if cfg.libraries else "(none)"
    return "\n".join([
        f"board:       {cfg.board}  (mcu={bs.mcu}, F_CPU={bs.f_cpu}, "
        f"baud={bs.baud}, variant={bs.variant})",
        f"core:        {cfg.core or '(not found)'}",
        f"variant:     {cfg.variant or '(not found)'}",
        f"avr-g++:     {cfg.gxx or '(not found)'}",
        f"avr-gcc:     {cfg.gcc or '(not found)'}",
        f"avr-objcopy: {cfg.objcopy or '(not found)'}",
        f"avr-size:    {cfg.size or '(not found)'}",
        f"avrdude:     {cfg.avrdude or '(not found)'}",
        f"avrdude.conf:{cfg.avrdude_conf or '(not found)'}",
        f"libraries:   {libs}",
        f"port:        {cfg.port or '(not set)'}",
    ])
