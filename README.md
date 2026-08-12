# Volt

**Volt** is a compiled programming language with Python-like syntax, built for programming Arduino boards and hardware modules directly. Instead of writing Arduino sketches in C++, Volt lets you write clean, indentation-based code that compiles down to C and then to a real binary flashed onto your board — no interpreter, no runtime overhead.

```volt
Arduino.Init()
myServo = Servo.Init(5)

myServo.write(90)
```

## Features

- **Python-like syntax** — indentation-based blocks, familiar keywords, no semicolons or braces
- **Compiled, not interpreted** — Volt source compiles to C, then to machine code via the standard AVR toolchain, so programs run at native speed
- **First-class hardware objects** — initialize hardware like `Arduino.Init()` or `Servo.Init(pin)` and store them in variables just like any other value
- **Built on the existing Arduino ecosystem** — hardware calls compile down to real Arduino/AVR C libraries under the hood

## Project Structure

```
volt-project/
├── src/        # Compiler source (lexer, parser, codegen)
├── voltc.py    # Volt compiler entry point
├── examples/   # Sample .volt programs
├── tests/      # Test programs / regression tests
├── docs/       # Language and hardware reference docs
└── build/      # Compiler output (generated, not tracked in git)
```

## Installation

There are two ways to use Volt: compiling the `voltc` compiler yourself, or installing the Volt IDE, which bundles the compiler and gives you a full editor with build/flash support.

### Option 1: Build the compiler from source

**Requirements:**
- Python 3.x (voltc is currently implemented in Python)
- `avr-gcc` and `avrdude` (or the Arduino CLI), for compiling and flashing to AVR-based Arduino boards

**Steps:**

```bash
# Clone the repository
git clone https://github.com/desalegnfitsum116-art/UnitStudio.git
cd UnitStudio

# Run the compiler directly
python voltc.py path/to/your_program.volt
```

This will compile your `.volt` file to C, then invoke the AVR toolchain to produce a flashable binary in the `build/` directory.

> A standalone packaged binary of `voltc` (no Python installation required) is planned for a future release.

### Option 2: Install the Volt IDE

The Volt IDE is a lightweight desktop editor purpose-built for Volt — syntax highlighting, one-click compile, board flashing, and a serial monitor, all bundled with the compiler so you don't need to install anything separately.

> 🔗 IDE repository link: **[to be added]**

Once available, installation will be:

```bash
# Clone the IDE repository
git clone <IDE repo URL>
cd <ide-folder>

# Install dependencies and run
npm install
npm start
```

Or download a prebuilt installer for your platform from the IDE repo's Releases page (once published).

## Usage

Write a `.volt` file:

```volt
Arduino.Init()
myServo = Servo.Init(5)

def sweep():
    myServo.write(0)
    myServo.write(90)
    myServo.write(180)

sweep()
```

Compile and flash:

```bash
python voltc.py sweep.volt --port COM3
```

(Exact flags depend on the current compiler CLI — see `docs/` for the full reference.)

## Status

Volt is under active development. The language spec, compiler, and IDE are being built in stages — check `docs/` for the current language reference and supported hardware modules.

## Contributing

Issues and pull requests are welcome once the core compiler pipeline stabilizes. See `docs/` for the language grammar if you'd like to help extend the compiler or add support for additional Arduino modules.

## License

*(Add your chosen license here — e.g. MIT, Apache 2.0.)*
