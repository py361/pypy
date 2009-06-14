# -*- coding: utf-8 -*-
import py
from pypy.interpreter.pyparser import pyparse
from pypy.interpreter.pyparser.pygram import syms, tokens
from pypy.interpreter.pyparser.error import SyntaxError, IndentationError


class TestPythonParser:

    def setup_class(self):
        self.parser = pyparse.PythonParser(self.space)

    def test_clear_state(self):
        assert self.parser.root is None
        tree = self.parser.parse_source("name = 32")
        assert self.parser.root is None

    def test_encoding(self):
        tree = self.parser.parse_source("""# coding: latin-1
stuff = "nothing"
""")
        assert tree.type == syms.encoding_decl
        assert tree.value == "iso-8859-1"
        sentence = u"u'Die Männer ärgen sich!'"
        input = (u"# coding: utf-7\nstuff = %s" % (sentence,)).encode("utf-7")
        tree = self.parser.parse_source(input)
        assert tree.value == "utf-7"
        input = "# coding: not-here"
        exc = py.test.raises(SyntaxError, self.parser.parse_source, input).value
        assert exc.msg == "Unknown encoding: not-here"

    def test_syntax_error(self):
        parse = self.parser.parse_source
        exc = py.test.raises(SyntaxError, parse, "name another for").value
        assert exc.msg == "invalid syntax"
        assert exc.lineno == 1
        assert exc.offset == 12
        assert exc.text == "name another for"
        exc = py.test.raises(SyntaxError, parse, "\"blah").value
        assert exc.msg == "EOL while scanning single-quoted string"
        exc = py.test.raises(SyntaxError, parse, "'''\n").value
        assert exc.msg == "EOF while scanning triple-quoted string"
        for input in ("())", "(()", "((", "))"):
            py.test.raises(SyntaxError, parse, input)

    def test_is(self):
        self.parser.parse_source("x is y")
        self.parser.parse_source("x is not y")

    def test_indentation_error(self):
        parse = self.parser.parse_source
        input = """
def f():
pass"""
        exc = py.test.raises(IndentationError, parse, input).value
        assert exc.msg == "expected indented block"
        assert exc.lineno == 3
        assert exc.text == "pass"
        assert exc.offset == 4
        input = "hi\n    indented"
        exc = py.test.raises(IndentationError, parse, input).value
        assert exc.msg == "unexpected indent"
        input = "def f():\n    pass\n  next_stmt"
        exc = py.test.raises(IndentationError, parse, input).value
        assert exc.msg == "unindent does not match any outer indentation level"

    def test_mode(self):
        assert self.parser.parse_source("x = 43*54").type == syms.file_input
        tree = self.parser.parse_source("43**54", "eval")
        assert tree.type == syms.eval_input
        py.test.raises(SyntaxError, self.parser.parse_source, "x = 54", "eval")
        tree = self.parser.parse_source("x = 43", "single")
        assert tree.type == syms.single_input
