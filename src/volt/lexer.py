"""Lexer for the Volt programming language.

Performs lexical analysis of Volt source text into a stream of :class:`Token`.
Handles:

* Identifiers and keywords
* Integer / float / string literals
* Operators and punctuation (incl. two-character operators)
* ``//`` line comments
* Indentation-based blocks (INDENT / DEDENT token generation)
* Newline statement terminators
* Precise error reporting with line/column information
"""

from .tokens import Token, TokenKind


class LexError(Exception):
    """Raised when Volt source cannot be tokenized.

    Attributes:
        message: Human-readable description.
        line:    1-based line of the offending text.
        col:     1-based column of the offending text.
        filename: Source file name (may be "<string>").
    """

    def __init__(self, message, line, col, filename="<string>"):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col
        self.filename = filename

    def __str__(self):
        return f"{self.filename}:{self.line}:{self.col}: lexical error: {self.message}"


# Reserved words -> token kind.
KEYWORDS = {
    "func": TokenKind.FUNC,
    "var": TokenKind.VAR,
    "if": TokenKind.IF,
    "elif": TokenKind.ELIF,
    "else": TokenKind.ELSE,
    "while": TokenKind.WHILE,
    "for": TokenKind.FOR,
    "return": TokenKind.RETURN,
    "import": TokenKind.IMPORT,
    "in": TokenKind.IN,
    "break": TokenKind.BREAK,
    "continue": TokenKind.CONTINUE,
    "true": TokenKind.TRUE,
    "false": TokenKind.FALSE,
    "null": TokenKind.NULL,
    "module": TokenKind.MODULE,
    "const": TokenKind.CONST,
    "not": TokenKind.NOT,
    "or": TokenKind.OR,
    "and": TokenKind.AND,
}

# Single- and multi-character operator / punctuation tokens.
# Scanned longest-match-first (order matters in this dict in Python 3.7+).
OPERATORS = {
    "==": TokenKind.EQ,
    "!=": TokenKind.NEQ,
    "<=": TokenKind.LE,
    ">=": TokenKind.GE,
    "+": TokenKind.PLUS,
    "-": TokenKind.MINUS,
    "*": TokenKind.STAR,
    "/": TokenKind.SLASH,
    "%": TokenKind.PERCENT,
    "=": TokenKind.ASSIGN,
    "<": TokenKind.LT,
    ">": TokenKind.GT,
    "!": TokenKind.BANG,
    ".": TokenKind.DOT,
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    "[": TokenKind.LBRACKET,
    "]": TokenKind.RBRACKET,
    ",": TokenKind.COMMA,
    ":": TokenKind.COLON,
    ";": TokenKind.SEMICOLON,
}

# Two-character operators checked before their single-character prefixes.
TWO_CHAR_OPS = {"==", "!=", "<=", ">="}

# Reserved prefixes for compiler-generated symbols (spec Appendix A).
RESERVED_PREFIXES = ("__volt_", "__builtin_")

# Escape sequences permitted inside string literals.
STRING_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "n": "\n",
    "t": "\t",
}

# Characters that begin an identifier (letter or underscore).
_ID_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
# Characters allowed to continue an identifier.
_ID_CONT = _ID_START | set("0123456789")


class Lexer:
    """Tokenizes Volt source text.

    Usage::

        lexer = Lexer(source, filename="main.volt")
        tokens = lexer.tokenize()
    """

    def __init__(self, source, filename="<string>"):
        self.src = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens = []

        # Indentation state.
        self.indent_stack = [0]
        self.at_line_start = True
        self.pending_indent = 0
        self.line_has_content = False

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def tokenize(self):
        """Scan the entire source and return a list of tokens (incl. EOF)."""
        while self.pos < len(self.src):
            c = self._peek()

            if c == "\n":
                self._handle_newline()
                continue

            if c in " \t":
                self._handle_whitespace()
                continue

            if self.at_line_start:
                if c == "/" and self._peek(1) == "/":
                    # Comment-only line: produce no tokens, no indent effect.
                    self._skip_line_comment()
                    continue
                self._resolve_indent()
                self.line_has_content = True
                # We have seen the first real token of this line.
                self.at_line_start = False

            self._lex_token()
            self.line_has_content = True

        self._finish()
        return self.tokens

    # ------------------------------------------------------------------ #
    # Low-level character helpers                                         #
    # ------------------------------------------------------------------ #

    def _peek(self, offset=0):
        index = self.pos + offset
        if index >= len(self.src):
            return "\0"
        return self.src[index]

    def _advance(self):
        ch = self.src[self.pos]
        self.pos += 1
        self.col += 1
        return ch

    def _error(self, message, line=None, col=None):
        raise LexError(
            message,
            self.line if line is None else line,
            self.col if col is None else col,
            self.filename,
        )

    def _emit(self, kind, lexeme, value=None, col=None):
        self.tokens.append(
            Token(kind, lexeme, self.line, self.col if col is None else col, value)
        )

    # ------------------------------------------------------------------ #
    # Indentation / newline handling                                      #
    # ------------------------------------------------------------------ #

    def _handle_whitespace(self):
        """Consume spaces/tabs. Tabs at the start of a line are rejected."""
        if self.at_line_start:
            while self.pos < len(self.src) and self._peek() in " \t":
                if self._peek() == "\t":
                    self._error(
                        "tabs are not allowed for indentation (use 4 spaces per level)"
                    )
                self._advance()
                self.pending_indent += 1
        else:
            while self.pos < len(self.src) and self._peek() in " \t":
                self._advance()

    def _resolve_indent(self):
        """Compare the current line's indentation against the stack.

        Emits INDENT when deeper, DEDENT(s) when shallower. A shallower
        indentation that does not match a previously used level is an error.
        """
        width = self.pending_indent
        top = self.indent_stack[-1]

        if width > top:
            self._emit(TokenKind.INDENT, "", col=width + 1)
            self.indent_stack.append(width)
        elif width < top:
            while self.indent_stack[-1] > width:
                self.indent_stack.pop()
                self._emit(TokenKind.DEDENT, "", col=width + 1)
            if self.indent_stack[-1] != width:
                self._error(
                    "inconsistent indentation: dedent does not match any "
                    "previously used indentation level"
                )

    def _handle_newline(self):
        """Emit a NEWLINE token for a line that produced tokens."""
        if self.line_has_content:
            self._emit(TokenKind.NEWLINE, "")
        self._advance()  # consume '\n'
        self.line += 1
        self.col = 1
        self.at_line_start = True
        self.line_has_content = False
        self.pending_indent = 0

    def _finish(self):
        """Emit final NEWLINE (if needed), closing DEDENTs, and EOF."""
        if self.line_has_content:
            self._emit(TokenKind.NEWLINE, "")
        while len(self.indent_stack) > 1:
            self.indent_stack.pop()
            self._emit(TokenKind.DEDENT, "")
        self._emit(TokenKind.EOF, "")

    # ------------------------------------------------------------------ #
    # Comment handling                                                    #
    # ------------------------------------------------------------------ #

    def _skip_line_comment(self):
        """Consume from '//' to (but not including) the end of the line."""
        while self.pos < len(self.src) and self._peek() != "\n":
            self._advance()

    # ------------------------------------------------------------------ #
    # Token scanning                                                      #
    # ------------------------------------------------------------------ #

    def _lex_token(self):
        c = self._peek()

        if c == "/" and self._peek(1) == "/":
            # Inline trailing comment: skip to end of line (NEWLINE still emitted).
            self._skip_line_comment()
            return

        if c in _ID_START:
            self._lex_identifier()
            return

        if c.isdigit():
            self._lex_number()
            return

        if c == '"':
            self._lex_string()
            return

        # Two-character operators.
        two = c + self._peek(1)
        if two in TWO_CHAR_OPS:
            start_col = self.col
            self._advance()
            self._advance()
            self._emit(OPERATORS[two], two, col=start_col)
            return

        if c in OPERATORS:
            start_col = self.col
            self._advance()
            self._emit(OPERATORS[c], c, col=start_col)
            return

        self._error(f"unexpected character {c!r}")

    def _lex_identifier(self):
        start_col = self.col
        start = self.pos
        while self.pos < len(self.src) and self._peek() in _ID_CONT:
            self._advance()
        text = self.src[start:self.pos]

        for prefix in RESERVED_PREFIXES:
            if text.startswith(prefix):
                self._error(
                    f"identifier {text!r} uses reserved prefix {prefix!r} "
                    "(reserved for compiler-generated symbols)"
                )

        kind = KEYWORDS.get(text, TokenKind.IDENT)
        self._emit(kind, text, col=start_col)

    def _lex_number(self):
        start_col = self.col
        start = self.pos
        while self.pos < len(self.src) and self._peek().isdigit():
            self._advance()

        if self._peek() == "." and self._peek(1).isdigit():
            # Float literal: digits '.' digits (at least one digit required
            # after the decimal point).
            self._advance()  # consume '.'
            while self.pos < len(self.src) and self._peek().isdigit():
                self._advance()
            text = self.src[start:self.pos]
            self._emit(TokenKind.FLOAT, text, value=float(text), col=start_col)
        elif self._peek() == ".":
            self._error(
                "expected at least one digit after the decimal point "
                "in float literal"
            )
        else:
            text = self.src[start:self.pos]
            self._emit(TokenKind.INT, text, value=int(text), col=start_col)

        # Guard against malformed literals like `123abc` or `1.2.3`.
        nxt = self._peek()
        if nxt.isalpha() or nxt == "_" or (nxt == "." and self._peek(1).isdigit()):
            self._error(
                f"unexpected character {nxt!r} after numeric literal "
                f"{self.src[start:self.pos]!r}"
            )

    def _lex_string(self):
        start_line = self.line
        start_col = self.col
        self._advance()  # consume opening '"'

        chars = []
        while True:
            if self.pos >= len(self.src):
                self._error("unterminated string literal", start_line, start_col)
            c = self._advance()
            if c == "\n":
                self._error("newline in string literal (multi-line strings are not supported)", start_line, start_col)
            if c == '"':
                break
            if c == "\\":
                esc = self._peek()
                if self.pos >= len(self.src):
                    self._error("unterminated escape sequence in string literal", self.line, self.col)
                if esc not in STRING_ESCAPES:
                    self._error(
                        f"unknown escape sequence '\\{esc}' "
                        f"(supported: \\\", \\\\, \\n, \\t)",
                        self.line,
                        self.col,
                    )
                self._advance()
                chars.append(STRING_ESCAPES[esc])
            else:
                chars.append(c)

        text = "".join(chars)
        self._emit(TokenKind.STRING, f'"{text}"', value=text, col=start_col)


def tokenize(source, filename="<string>"):
    """Convenience wrapper: lex ``source`` and return the token list."""
    return Lexer(source, filename).tokenize()
