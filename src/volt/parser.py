"""Recursive-descent parser for Volt.

Turns the token stream produced by :class:`volt.lexer.Lexer` into an AST
(:mod:`volt.ast`). Indentation is handled through the INDENT/DEDENT tokens
emitted by the lexer.

Grammar implemented (see docs/volt-language-spec.md, Section 4): statements,
function/module/import declarations, type annotations (with optional colon:
both ``var x: int`` and ``var x int`` are accepted), expression precedence,
postfix chains (member access, calls, indexing).
"""

from __future__ import annotations

from .ast import (
    Program,
    ImportStmt,
    ModuleDecl,
    FuncDecl,
    Param,
    VarDecl,
    Assign,
    ExprStmt,
    ReturnStmt,
    IfStmt,
    IfBranch,
    WhileStmt,
    ForStmt,
    BreakStmt,
    ContinueStmt,
    TypeRef,
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
from .tokens import Token, TokenKind

# Type keyword names that can appear in annotation position. Note these are
# NOT lexer-reserved; they are ordinary identifiers interpreted contextually.
# Module type names (Arduino, Servo, DigitalPin, ...) are also plain identifiers.
PRIMITIVE_TYPES = {"int", "float", "bool", "string"}


class ParseError(Exception):
    """Raised when Volt source cannot be parsed."""

    def __init__(self, message, line, col, filename="<string>"):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col
        self.filename = filename

    def __str__(self):
        return f"{self.filename}:{self.line}:{self.col}: syntax error: {self.message}"


class Parser:
    def __init__(self, tokens, filename="<string>"):
        self.tokens = tokens
        self.filename = filename
        self.pos = 0

    # ------------------------------------------------------------------ #
    # Token helpers                                                       #
    # ------------------------------------------------------------------ #

    @property
    def here(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, offset=1) -> Token:
        index = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def at(self, *kinds) -> bool:
        return self.here.kind in kinds

    def match(self, kind) -> Token | None:
        if self.at(kind):
            return self.advance()
        return None

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.kind is not TokenKind.EOF:
            self.pos += 1
        return tok

    def expect(self, kind, what=None) -> Token:
        if self.at(kind):
            return self.advance()
        self.error(f"expected {what or kind.name.lower()}")

    def error(self, message):
        raise ParseError(message, self.here.line, self.here.col, self.filename)

    def skip_newlines(self):
        while self.at(TokenKind.NEWLINE):
            self.advance()

    def consume_statement_end(self):
        """Consume the terminator of a simple statement: ';' or NEWLINE(s)."""
        if self.match(TokenKind.SEMICOLON):
            self.skip_newlines()
        elif self.at(TokenKind.NEWLINE):
            self.advance()
            self.skip_newlines()
        elif self.at(TokenKind.EOF):
            pass
        else:
            self.error("expected end of statement (newline or ';')")

    # ------------------------------------------------------------------ #
    # Top level                                                           #
    # ------------------------------------------------------------------ #

    def parse_program(self) -> Program:
        start = self.here
        self.skip_newlines()
        statements = []
        while not self.at(TokenKind.EOF):
            statements.append(self.parse_statement())
            self.skip_newlines()
        return Program(statements=statements, line=start.line, col=start.col)

    # ------------------------------------------------------------------ #
    # Statements                                                          #
    # ------------------------------------------------------------------ #

    def parse_statement(self):
        tok = self.here
        if self.at(TokenKind.IMPORT):
            return self.parse_import()
        if self.at(TokenKind.MODULE):
            return self.parse_module()
        if self.at(TokenKind.FUNC):
            return self.parse_func()
        if self.at(TokenKind.VAR):
            return self.parse_var_decl()
        if self.at(TokenKind.IF):
            return self.parse_if()
        if self.at(TokenKind.WHILE):
            return self.parse_while()
        if self.at(TokenKind.FOR):
            return self.parse_for()
        if self.at(TokenKind.RETURN):
            self.advance()
            value = None
            if not self.at(TokenKind.NEWLINE, TokenKind.SEMICOLON, TokenKind.EOF):
                value = self.parse_expression()
            self.consume_statement_end()
            return ReturnStmt(value=value, line=tok.line, col=tok.col)
        if self.at(TokenKind.BREAK):
            self.advance()
            self.consume_statement_end()
            return BreakStmt(line=tok.line, col=tok.col)
        if self.at(TokenKind.CONTINUE):
            self.advance()
            self.consume_statement_end()
            return ContinueStmt(line=tok.line, col=tok.col)
        return self.parse_expr_or_assign()

    def parse_expr_or_assign(self):
        tok = self.here
        target = self.parse_expression()
        if self.match(TokenKind.ASSIGN):
            value = self.parse_expression()
            self.consume_statement_end()
            return Assign(target=target, value=value, line=tok.line, col=tok.col)
        self.consume_statement_end()
        return ExprStmt(expr=target, line=tok.line, col=tok.col)

    # ------------------------------------------------------------------ #
    # Declarations                                                        #
    # ------------------------------------------------------------------ #

    def parse_import(self):
        tok = self.here
        self.expect(TokenKind.IMPORT)
        name = self.expect(TokenKind.IDENT, "module name after 'import'")
        self.consume_statement_end()
        return ImportStmt(name=name.lexeme, line=tok.line, col=tok.col)

    def parse_module(self):
        tok = self.here
        self.expect(TokenKind.MODULE)
        name = self.expect(TokenKind.IDENT, "module name after 'module'")
        self.expect(TokenKind.COLON)
        body = self.parse_block()
        self.skip_newlines()
        return ModuleDecl(name=name.lexeme, body=body, line=tok.line, col=tok.col)

    def parse_func(self):
        tok = self.here
        self.expect(TokenKind.FUNC)
        name = self.expect(TokenKind.IDENT, "function name after 'func'")
        self.expect(TokenKind.LPAREN)

        params = []
        if not self.at(TokenKind.RPAREN):
            params.append(self.parse_param())
            while self.match(TokenKind.COMMA):
                params.append(self.parse_param())

        self.expect(TokenKind.RPAREN)

        return_type = None
        if self.at(TokenKind.MINUS) and self.peek().kind is TokenKind.GT:
            self.advance()
            self.advance()
            return_type = self.parse_type()

        self.expect(TokenKind.COLON)
        body = self.parse_block()
        self.skip_newlines()
        return FuncDecl(
            name=name.lexeme,
            params=params,
            return_type=return_type,
            body=body,
            line=tok.line,
            col=tok.col,
        )

    def parse_param(self):
        tok = self.here
        name = self.expect(TokenKind.IDENT, "parameter name")
        param_type = None
        if self.match(TokenKind.COLON):
            param_type = self.parse_type()
        elif self.at(TokenKind.IDENT):
            # No-colon annotation form: `func f(a int)`.
            param_type = self.parse_type()
        return Param(name=name.lexeme, type=param_type, line=tok.line, col=tok.col)

    def parse_var_decl(self):
        tok = self.here
        self.expect(TokenKind.VAR)
        name = self.expect(TokenKind.IDENT, "variable name after 'var'")
        decl_type = None
        if self.match(TokenKind.COLON):
            decl_type = self.parse_type()
        elif self.at(TokenKind.IDENT):
            # No-colon annotation form: `var x int = 0`.
            decl_type = self.parse_type()
        value = None
        if self.match(TokenKind.ASSIGN):
            value = self.parse_expression()
        self.consume_statement_end()
        return VarDecl(name=name.lexeme, type=decl_type, value=value, line=tok.line, col=tok.col)

    # ------------------------------------------------------------------ #
    # Types                                                               #
    # ------------------------------------------------------------------ #

    def parse_type(self) -> TypeRef:
        tok = self.here
        name = self.expect(TokenKind.IDENT, "a type name")
        array = False
        size = None
        if self.match(TokenKind.LBRACKET):
            array = True
            if self.at(TokenKind.INT):
                size = self.advance().value
            self.expect(TokenKind.RBRACKET, "']'")
        return TypeRef(base=name.lexeme, array=array, size=size, line=tok.line, col=tok.col)

    # ------------------------------------------------------------------ #
    # Control flow                                                        #
    # ------------------------------------------------------------------ #

    def parse_if(self):
        tok = self.here
        self.expect(TokenKind.IF)
        cond = self.parse_expression()
        self.expect(TokenKind.COLON)
        body = self.parse_block()
        branches = [IfBranch(keyword="if", condition=cond, body=body, line=tok.line, col=tok.col)]

        self.skip_newlines()
        while self.at(TokenKind.ELIF):
            bt = self.here
            self.advance()
            c = self.parse_expression()
            self.expect(TokenKind.COLON)
            b = self.parse_block()
            branches.append(IfBranch(keyword="elif", condition=c, body=b, line=bt.line, col=bt.col))
            self.skip_newlines()

        else_body = None
        if self.match(TokenKind.ELSE):
            self.expect(TokenKind.COLON)
            else_body = self.parse_block()

        self.skip_newlines()
        return IfStmt(branches=branches, else_body=else_body, line=tok.line, col=tok.col)

    def parse_while(self):
        tok = self.here
        self.expect(TokenKind.WHILE)
        cond = self.parse_expression()
        self.expect(TokenKind.COLON)
        body = self.parse_block()
        self.skip_newlines()
        return WhileStmt(condition=cond, body=body, line=tok.line, col=tok.col)

    def parse_for(self):
        tok = self.here
        self.expect(TokenKind.FOR)
        var_name = self.expect(TokenKind.IDENT, "loop variable after 'for'")
        self.expect(TokenKind.IN, "'in'")
        iterable = self.parse_expression()
        self.expect(TokenKind.COLON)
        body = self.parse_block()
        self.skip_newlines()
        return ForStmt(var_name=var_name.lexeme, iterable=iterable, body=body, line=tok.line, col=tok.col)

    def parse_block(self):
        """Parse an indented block after a colon."""
        if not self.at(TokenKind.NEWLINE):
            self.error("expected newline and an indented block after ':'")
        self.advance()
        self.skip_newlines()
        self.expect(TokenKind.INDENT, "an indented block")
        body = []
        while not self.at(TokenKind.DEDENT):
            if self.at(TokenKind.EOF):
                self.error("unexpected end of file inside a block (missing dedent)")
            body.append(self.parse_statement())
        self.advance()  # consume DEDENT
        return body

    # ------------------------------------------------------------------ #
    # Expressions                                                         #
    # ------------------------------------------------------------------ #

    def parse_expression(self):
        return self.parse_or()

    def parse_or(self):
        expr = self.parse_and()
        while self.at(TokenKind.OR):
            tok = self.advance()
            right = self.parse_and()
            expr = Binary(op=tok.lexeme, left=expr, right=right, line=expr.line, col=expr.col)
        return expr

    def parse_and(self):
        expr = self.parse_not()
        while self.at(TokenKind.AND):
            tok = self.advance()
            right = self.parse_not()
            expr = Binary(op=tok.lexeme, left=expr, right=right, line=expr.line, col=expr.col)
        return expr

    def parse_not(self):
        if self.at(TokenKind.NOT):
            tok = self.advance()
            operand = self.parse_not()
            return Unary(op="not", operand=operand, line=tok.line, col=tok.col)
        return self.parse_comparison()

    def parse_comparison(self):
        expr = self.parse_additive()
        while self.at(
            TokenKind.EQ, TokenKind.NEQ, TokenKind.LT,
            TokenKind.GT, TokenKind.LE, TokenKind.GE,
        ):
            tok = self.advance()
            right = self.parse_additive()
            expr = Binary(op=tok.lexeme, left=expr, right=right, line=expr.line, col=expr.col)
        return expr

    def parse_additive(self):
        expr = self.parse_multiplicative()
        while self.at(TokenKind.PLUS, TokenKind.MINUS):
            tok = self.advance()
            right = self.parse_multiplicative()
            expr = Binary(op=tok.lexeme, left=expr, right=right, line=expr.line, col=expr.col)
        return expr

    def parse_multiplicative(self):
        expr = self.parse_unary()
        while self.at(TokenKind.STAR, TokenKind.SLASH, TokenKind.PERCENT):
            tok = self.advance()
            right = self.parse_unary()
            expr = Binary(op=tok.lexeme, left=expr, right=right, line=expr.line, col=expr.col)
        return expr

    def parse_unary(self):
        if self.at(TokenKind.MINUS):
            tok = self.advance()
            operand = self.parse_unary()
            return Unary(op="-", operand=operand, line=tok.line, col=tok.col)
        if self.at(TokenKind.BANG):
            tok = self.advance()
            operand = self.parse_unary()
            return Unary(op="!", operand=operand, line=tok.line, col=tok.col)
        return self.parse_postfix()

    def parse_postfix(self):
        expr = self.parse_primary()
        while True:
            if self.match(TokenKind.LPAREN):
                args = []
                if not self.at(TokenKind.RPAREN):
                    args.append(self.parse_expression())
                    while self.match(TokenKind.COMMA):
                        args.append(self.parse_expression())
                self.expect(TokenKind.RPAREN, "')'")
                expr = Call(callee=expr, args=args, line=expr.line, col=expr.col)
            elif self.match(TokenKind.DOT):
                name = self.expect(TokenKind.IDENT, "member name after '.'")
                expr = Member(obj=expr, name=name.lexeme, line=expr.line, col=expr.col)
            elif self.match(TokenKind.LBRACKET):
                index = self.parse_expression()
                self.expect(TokenKind.RBRACKET, "']'")
                expr = Index(obj=expr, index=index, line=expr.line, col=expr.col)
            else:
                break
        return expr

    def parse_primary(self):
        tok = self.here
        if self.at(TokenKind.INT):
            self.advance()
            return IntLit(value=tok.value, line=tok.line, col=tok.col)
        if self.at(TokenKind.FLOAT):
            self.advance()
            return FloatLit(value=tok.value, line=tok.line, col=tok.col)
        if self.at(TokenKind.STRING):
            self.advance()
            return StrLit(value=tok.value, line=tok.line, col=tok.col)
        if self.at(TokenKind.TRUE):
            self.advance()
            return BoolLit(value=True, line=tok.line, col=tok.col)
        if self.at(TokenKind.FALSE):
            self.advance()
            return BoolLit(value=False, line=tok.line, col=tok.col)
        if self.at(TokenKind.NULL):
            self.advance()
            return NullLit(line=tok.line, col=tok.col)
        if self.at(TokenKind.IDENT):
            self.advance()
            return Ident(name=tok.lexeme, line=tok.line, col=tok.col)
        if self.at(TokenKind.LPAREN):
            self.advance()
            expr = self.parse_expression()
            self.expect(TokenKind.RPAREN, "')'")
            return expr
        if self.at(TokenKind.LBRACKET):
            self.advance()
            elements = []
            if not self.at(TokenKind.RBRACKET):
                elements.append(self.parse_expression())
                while self.match(TokenKind.COMMA):
                    elements.append(self.parse_expression())
            self.expect(TokenKind.RBRACKET, "']'")
            return ArrayLit(elements=elements, line=tok.line, col=tok.col)
        self.error("expected an expression")


def parse(tokens, filename="<string>"):
    """Convenience wrapper: parse a token list into a Program AST."""
    return Parser(tokens, filename).parse_program()
