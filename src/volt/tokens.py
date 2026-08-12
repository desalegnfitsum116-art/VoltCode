"""Token and TokenKind definitions for the Volt lexer."""

from enum import Enum, auto


class TokenKind(Enum):
    # --- Literals / identifiers -------------------------------------------
    IDENT = auto()          # foo, myServo, Arduino
    INT = auto()            # 42
    FLOAT = auto()          # 3.14
    STRING = auto()         # "hello"

    # --- Keywords -----------------------------------------------------------
    FUNC = auto()           # func
    VAR = auto()            # var
    IF = auto()             # if
    ELIF = auto()           # elif
    ELSE = auto()           # else
    WHILE = auto()          # while
    FOR = auto()            # for
    RETURN = auto()         # return
    IMPORT = auto()         # import
    IN = auto()             # in
    BREAK = auto()          # break
    CONTINUE = auto()       # continue
    TRUE = auto()           # true
    FALSE = auto()          # false
    NULL = auto()           # null
    MODULE = auto()         # module
    CONST = auto()          # const
    NOT = auto()            # not
    OR = auto()             # or
    AND = auto()            # and

    # --- Punctuation / operators ---------------------------------------------
    PLUS = auto()           # +
    MINUS = auto()          # -
    STAR = auto()           # *
    SLASH = auto()          # /
    PERCENT = auto()        # %
    ASSIGN = auto()         # =
    EQ = auto()             # ==
    NEQ = auto()            # !=
    LT = auto()             # <
    GT = auto()             # >
    LE = auto()             # <=
    GE = auto()             # >=
    BANG = auto()           # !
    DOT = auto()            # .
    LPAREN = auto()         # (
    RPAREN = auto()         # )
    LBRACKET = auto()       # [
    RBRACKET = auto()       # ]
    COMMA = auto()          # ,
    COLON = auto()          # :
    SEMICOLON = auto()      # ;

    # --- Structural ------------------------------------------------------------
    NEWLINE = auto()        # logical line terminator
    INDENT = auto()         # block begin
    DEDENT = auto()         # block end
    EOF = auto()            # end of file


class Token:
    """A single lexical token.

    Attributes:
        kind:   TokenKind of this token.
        lexeme: The raw source text that produced this token.
        value:  Semantic value; for INT it is an int, for FLOAT a float, for
                STRING the decoded string contents, otherwise None.
        line:   1-based line number.
        col:    1-based column number (character-based, not display-aware).
    """

    __slots__ = ("kind", "lexeme", "value", "line", "col")

    def __init__(self, kind, lexeme, line, col, value=None):
        self.kind = kind
        self.lexeme = lexeme
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        base = f"{self.kind.name}('{self.lexeme}')"
        if self.value is not None:
            base += f" = {self.value!r}"
        return base

    def short_str(self):
        """Compact one-line rendering used for token-stream dumps."""
        label = self.kind.name
        if self.kind in (TokenKind.IDENT, TokenKind.INT, TokenKind.FLOAT, TokenKind.STRING):
            label = f"{self.kind.name}({self.lexeme})"
        return f"{label}@{self.line}:{self.col}"
