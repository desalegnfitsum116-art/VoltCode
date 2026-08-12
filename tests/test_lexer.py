"""Standalone tests for the Volt lexer.

Run with:  python tests/test_lexer.py
No external dependencies required.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from volt.lexer import LexError, Lexer  # noqa: E402
from volt.tokens import TokenKind  # noqa: E402

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


def _tokens(source):
    if isinstance(source, list):
        return source
    return Lexer(source).tokenize()


def kinds(source):
    return [t.kind for t in _tokens(source)]


def kind_stream(source):
    return [t.kind.name for t in _tokens(source)]


def lex_error(source):
    try:
        Lexer(source).tokenize()
    except LexError:
        return True
    return False


def test_basic_literals():
    print("== literals ==")
    toks = Lexer("var x = 42").tokenize()
    int_tok = [t for t in toks if t.kind is TokenKind.INT]
    check("int literal value", len(int_tok) == 1 and int_tok[0].value == 42)

    toks = Lexer("var f = 3.14").tokenize()
    flt_tok = [t for t in toks if t.kind is TokenKind.FLOAT]
    check("float literal value", len(flt_tok) == 1 and flt_tok[0].value == 3.14)

    toks = Lexer('var s = "hello"').tokenize()
    str_tok = [t for t in toks if t.kind is TokenKind.STRING]
    check("string literal value", len(str_tok) == 1 and str_tok[0].value == "hello")

    toks = Lexer(r'var s = "a\nb\t\"c\\"').tokenize()
    str_tok = [t for t in toks if t.kind is TokenKind.STRING]
    check(
        "string escapes decoded",
        len(str_tok) == 1 and str_tok[0].value == 'a\nb\t"c\\',
    )


def test_comments():
    print("== comments ==")
    toks = Lexer("var x = 1 // trailing comment\nvar y = 2").tokenize()
    check("trailing comment skipped", "COMMENT" not in [t.kind.name for t in toks])

    toks = Lexer("// only a comment\n").tokenize()
    check("comment-only line yields no tokens", kinds("// only a comment\n") == [TokenKind.EOF])

    toks = Lexer("var x = 1 // comment\n\n\nvar y = 2 // more\n").tokenize()
    check("comments + blank lines skipped", "COMMENT" not in [t.kind.name for t in toks])


def test_keywords_and_operators():
    print("== keywords & operators ==")
    src = "func if elif else while for return import in break continue true false null module const not or and"
    ks = kind_stream(Lexer(src).tokenize())
    expected = [
        "FUNC", "IF", "ELIF", "ELSE", "WHILE", "FOR", "RETURN", "IMPORT",
        "IN", "BREAK", "CONTINUE", "TRUE", "FALSE", "NULL", "MODULE",
        "CONST", "NOT", "OR", "AND", "NEWLINE", "EOF",
    ]
    check("all keywords recognized", ks == expected)

    src = "+ - * / % = == != < > <= >= ! . ( ) [ ] , : ;"
    ks = kind_stream(Lexer(src).tokenize())
    expected = [
        "PLUS", "MINUS", "STAR", "SLASH", "PERCENT", "ASSIGN", "EQ", "NEQ",
        "LT", "GT", "LE", "GE", "BANG", "DOT", "LPAREN", "RPAREN",
        "LBRACKET", "RBRACKET", "COMMA", "COLON", "SEMICOLON",
        "NEWLINE", "EOF",
    ]
    check("all operators recognized", ks == expected)


def test_indentation():
    print("== indentation ==")
    src = "if x == 1:\n    var y = 2\n    var z = 3\nvar w = 4\n"
    ks = kind_stream(Lexer(src).tokenize())
    check("INDENT on block start", "INDENT" in ks)
    check("DEDENT on block end", "DEDENT" in ks)
    check(
        "INDENT before DEDENT",
        ks.index("INDENT") < ks.index("DEDENT"),
    )

    src = "func f():\n    if x:\n        var a = 1\n    var b = 2\nvar c = 3\n"
    ks = kind_stream(Lexer(src).tokenize())
    check("nested indent count", ks.count("INDENT") == 2)
    check("nested dedent count", ks.count("DEDENT") == 2)

    src = "if a:\n    if b:\n        var x = 1\nvar y = 2\n"
    ks = kind_stream(Lexer(src).tokenize())
    check("double dedent on block exit", ks.count("DEDENT") == 2)


def test_indent_errors():
    print("== indentation errors ==")
    check("tab indentation rejected", lex_error("if x:\n\tvar y = 1\n"))
    check("inconsistent dedent rejected", lex_error("if x:\n    if y:\n        var a = 1\n  var b = 2\n"))


def test_statement_terminators():
    print("== statement terminators ==")
    src = "var a = 1; var b = 2\nvar c = 3\n"
    ks = kind_stream(Lexer(src).tokenize())
    check("semicolon separator tokenized", "SEMICOLON" in ks)
    check("single NEWLINE for two-var line + one", ks.count("NEWLINE") == 2)

    src = "var a = 1\n"
    ks = kind_stream(Lexer(src).tokenize())
    check("trailing newline handled", ks == ["VAR", "IDENT", "ASSIGN", "INT", "NEWLINE", "EOF"])


def test_hardware_pattern():
    print("== hardware init pattern ==")
    src = (
        "var myBoard = Arduino.Init()\n"
        "var myServo = Servo.Init(5)\n"
        "myServo.write(90)\n"
    )
    ks = kind_stream(Lexer(src).tokenize())
    expected = [
        "VAR", "IDENT", "ASSIGN", "IDENT", "DOT", "IDENT", "LPAREN", "RPAREN", "NEWLINE",
        "VAR", "IDENT", "ASSIGN", "IDENT", "DOT", "IDENT", "LPAREN", "INT", "RPAREN", "NEWLINE",
        "IDENT", "DOT", "IDENT", "LPAREN", "INT", "RPAREN", "NEWLINE",
        "EOF",
    ]
    check("hardware init pattern tokenizes correctly", ks == expected)


def test_errors():
    print("== lexical errors ==")
    check("unknown character", lex_error("var x = $"))
    check("unterminated string", lex_error('var s = "abc'))
    check("newline in string", lex_error('var s = "ab\nc"'))
    check("unknown escape", lex_error(r'var s = "\q"'))
    check("bad float (no trailing digits)", lex_error("var f = 3."))
    check("number followed by identifier", lex_error("var x = 123abc"))
    check("float followed by float", lex_error("var x = 1.2.3"))
    check("reserved __volt_ prefix", lex_error("var __volt_x = 1"))


def test_positions():
    print("== position tracking ==")
    src = "var a = 1\nvar bb = 22\n"
    toks = Lexer(src).tokenize()
    ident_bb = [t for t in toks if t.kind is TokenKind.IDENT and t.lexeme == "bb"][0]
    check("line tracked", ident_bb.line == 2)
    check("column tracked", ident_bb.col == 5)


def test_multiple_statements_are_separate_lines():
    print("== multiline ==")
    src = "var a = 1\n\n\nvar b = 2\n"
    ks = kind_stream(Lexer(src).tokenize())
    check("blank lines skipped", ks.count("NEWLINE") == 2)


def main():
    test_basic_literals()
    test_comments()
    test_keywords_and_operators()
    test_indentation()
    test_indent_errors()
    test_statement_terminators()
    test_hardware_pattern()
    test_errors()
    test_positions()
    test_multiple_statements_are_separate_lines()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
