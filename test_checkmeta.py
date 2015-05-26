import unittest
import checkmeta
import codecs
from tempfile import NamedTemporaryFile


class UniMock(object):

    class DummyCallable(object):
        def __init__(self, owner, name):
            self.__owner = owner
            self.__name = name

        def __call__(self, *args, **kwargs):
            self.__owner.registerCall(self.__name, tuple(args))

    def __init__(self):
        self.__called = {}

    def __getattribute__(self, name):
        if not name.startswith("_") and name not in dir(self):
            return UniMock.DummyCallable(self, name)
        else:
            return object.__getattribute__(self, name)

    def registerCall(self, name, args):
        if name not in self.__called:
            self.__called[name] = []
        self.__called[name].append(args)

    def numCalls(self, name, args=None):
        calls = self.__called.get(name, [])
        if args is None:
            return len(calls)
        else:
            return sum(1 for call in calls if call == args)


class URLCombineTestCase(unittest.TestCase):

    def setUp(self):
        self.__syntaxes = {
            '1': 'syn1:',
            '2': 'syn2:'
        }

    def test_parseExpression_acceptsValidExpression(self):
        parse = checkmeta.ExpressionParser()
        self.assertEqual("glob:file.cpp",
                         parse("file.cpp", 'glob'))
        # glob
        self.assertEqual("glob:dir/*.cpp",
                         parse("dir/*.cpp", 'glob'))
        # re
        self.assertEqual("glob:dir/[a-z]*.cpp",
                         parse("dir/[a-z]*.cpp", 'glob'))
        self.assertEqual("glob:dir/[a-z:]*.cpp",
                         parse("dir/[a-z:]*.cpp", 'glob'))

    def test_parseExpression_acceptsLocalSyntaxOverride(self):
        parse = checkmeta.ExpressionParser()
        # local syntax override
        self.assertEqual("relre:file.cpp", parse("re:file.cpp", 'glob'))
        self.assertEqual("relre:file.cpp", parse("relre:file.cpp", 'glob'))

    def test_parseExpression_rejectsInvalidSyntax(self):
        parse = checkmeta.ExpressionParser()
        with self.assertRaises(KeyError):
            parse("invalid:file.cpp", "glob:")

    def test_parseExpression_rejectsInvalid(self):
        # currently there are no invalid expressions
        pass

    def test_parseExpression_invertedSyntaxesCorrect(self):
        checkmeta.ExpressionParser.invertedSyntaxes()['relglob'] == 'glob'

    def test_parseChecks_acceptsValidFunction(self):
        ui = UniMock()
        parse = checkmeta.CheckParser(ui)
        checks = parse("encoding('utf-8')")
        self.assertIsInstance(checks, list)
        self.assertEqual(len(checks), 1)
        self.assertTrue(callable(checks[0][1]), "non-callable result")
        self.assertEqual(0, ui.numCalls("warn"))  # no warnings

    def test_parseChecks_acceptsMultipleValidFunctions(self):
        ui = UniMock()
        parse = checkmeta.CheckParser(ui)
        checks = parse("encoding('utf-8') mimetype('text/plain')")
        self.assertIsInstance(checks, list)
        self.assertEqual(len(checks), 2)
        for func in checks:
            self.assertTrue(callable(func[1]), "non-callable result")
        self.assertEqual(0, ui.numCalls("warn"))  # no warnings

    def test_parseChecks_skipsInvalidFunction(self):
        ui = UniMock()
        parse = checkmeta.CheckParser(ui)
        checks = parse("encoding('utf-8') invalid() mimetype('text/plain')")
        self.assertIsInstance(checks, list)
        self.assertEqual(len(checks), 2)
        for func in checks:
            self.assertTrue(callable(func[1]), "non-callable result")
        self.assertEqual(1, ui.numCalls("warn"))

    def test_parseChecks_rejectsInvalidSyntax(self):
        ui = UniMock()
        parse = checkmeta.CheckParser(ui)
        func = parse("invalid[]")
        self.assertEqual(len(func), 0)
        self.assertEqual(1, ui.numCalls("warn"))

    def test_parseCheck_emptyCheckListSupported(self):
        ui = UniMock()
        funcs = checkmeta.CheckParser(ui)("")
        self.assertIsInstance(funcs, list)
        self.assertEqual(0, len(funcs))
        self.assertEqual(0, ui.numCalls("warn"))

    def test_parseCheck_nodecodeWorks(self):
        ui = UniMock()
        parser = checkmeta.CheckParser(ui, decodeChecks=False)
        self.assertEqual(["encoding(ascii)", "bom(true)"],
                         parser("encoding(ascii) bom(true)"))

    def test_encodingTest_acceptsValidData(self):
        self.assertIsNone(checkmeta.encodingTest('ascii', "abcd", {}))
        self.assertIsNone(checkmeta.encodingTest('utf-8', "abc\xc3\xa4", {}))

    def test_encodingTest_rejectsInvalidData(self):
        self.assertIsNotNone(checkmeta.encodingTest('ascii', "abc\x80", {}))
        self.assertIsNotNone(checkmeta.encodingTest('utf-8', "abc\x80", {}))

    def test_mimeTypeTest_noTestForBinary(self):
        self.assertIsNone(checkmeta.mimeTest("image/png", "", {}))

    def test_mimeTypeTest_noTestIfEncodingUnknown(self):
        self.assertIsNone(checkmeta.mimeTest("text/plain", "\x80", {}))
        self.assertIsNone(checkmeta.mimeTest("text/plain", "\x80",
                                             {"encoding": ("binary",)}))

    def test_mimeTypeTest_detectsInvalid(self):
        # bell character is part of utf-8 and ascii but not printable
        self.assertIsNotNone(checkmeta.mimeTest("text/plain", "\x07",
                                                {"encoding": ("utf-8",)}))
        self.assertIsNotNone(checkmeta.mimeTest("text/plain", "\x07",
                                                {"encoding": ("ascii",)}))

    def test_mimeTypeTest_allowsValid(self):
        self.assertIsNone(checkmeta.mimeTest("text/plain", "valid",
                                             {"encoding": ("ascii",)}))

    def test_bmpTest_rejectsInvalid(self):
        data = u"" + unichr(0x10000)
        self.assertIsNotNone(checkmeta.bmpTest(data.encode("utf-8"),
                                               {"encoding": ("utf-8",)}))

    def test_bmpTest_failIfMissingEncoding(self):
        self.assertIsNotNone(checkmeta.bmpTest("", {}))

    def test_bmpTest_failIfWrongEncoding(self):
        # ascii can't have a bom
        self.assertIsNotNone(checkmeta.bmpTest("", {"encoding": ("ascii",)}))

    def test_bmpTest_allowsValid(self):
        self.assertIsNone(checkmeta.bmpTest("\uffff", {"encoding": ("utf-8",)}))

    def test_bomTest_rejectsMissing(self):
        self.assertIsNotNone(checkmeta.bomTest("true", "",
                                               {"encoding": ("utf-8",)}))

    def test_bomTest_failIfMissingEncoding(self):
        self.assertIsNotNone(checkmeta.bomTest("true", "", {}))

    def test_bomTest_failIfWrongEncoding(self):
        self.assertIsNotNone(checkmeta.bomTest("true", "",
                                               {"encoding": ("ascii",)}))

    def test_bomTest_rejectsWrong(self):
        self.assertIsNotNone(checkmeta.bomTest("true", codecs.BOM_UTF16_LE,
                                               {"encoding": ("utf8",)}))
        self.assertIsNotNone(checkmeta.bomTest("true", codecs.BOM_UTF16_BE,
                                               {"encoding": ("utf8",)}))
        self.assertIsNotNone(checkmeta.bomTest("true", codecs.BOM_UTF32_LE,
                                               {"encoding": ("utf8",)}))
        self.assertIsNotNone(checkmeta.bomTest("true", codecs.BOM_UTF32_BE,
                                               {"encoding": ("utf8",)}))
        self.assertIsNotNone(checkmeta.bomTest("true", codecs.BOM_UTF8,
                                               {"encoding": ("utf16",)}))
        self.assertIsNotNone(checkmeta.bomTest("true", codecs.BOM_UTF32_LE,
                                               {"encoding": ("utf16",)}))
        self.assertIsNotNone(checkmeta.bomTest("true", codecs.BOM_UTF32_BE,
                                               {"encoding": ("utf16",)}))
        self.assertIsNotNone(checkmeta.bomTest("true", codecs.BOM_UTF8,
                                               {"encoding": ("utf32",)}))
        self.assertIsNotNone(checkmeta.bomTest("true", codecs.BOM_UTF16_LE,
                                               {"encoding": ("utf32",)}))
        self.assertIsNotNone(checkmeta.bomTest("true", codecs.BOM_UTF16_BE,
                                               {"encoding": ("utf32",)}))

    def test_bomTest_acceptsCorrect(self):
        self.assertIsNone(checkmeta.bomTest("true", codecs.BOM_UTF8,
                                            {"encoding": ("utf8",)}))
        self.assertIsNone(checkmeta.bomTest("true", codecs.BOM_UTF16_LE,
                                            {"encoding": ("utf16",)}))
        self.assertIsNone(checkmeta.bomTest("true", codecs.BOM_UTF16_BE,
                                            {"encoding": ("utf16",)}))
        self.assertIsNone(checkmeta.bomTest("true", codecs.BOM_UTF32_LE,
                                            {"encoding": ("utf32",)}))
        self.assertIsNone(checkmeta.bomTest("true", codecs.BOM_UTF32_BE,
                                            {"encoding": ("utf32",)}))

    def test_bomTest_falseRejectsWithBom(self):
        self.assertIsNotNone(checkmeta.bomTest("false", codecs.BOM_UTF8, {}))
        self.assertIsNotNone(checkmeta.bomTest("false", codecs.BOM_UTF16_LE, {}))
        self.assertIsNotNone(checkmeta.bomTest("false", codecs.BOM_UTF16_BE, {}))
        self.assertIsNotNone(checkmeta.bomTest("false", codecs.BOM_UTF32_LE, {}))
        self.assertIsNotNone(checkmeta.bomTest("false", codecs.BOM_UTF32_BE, {}))

    def test_bomTest_falseAcceptsWithoutBom(self):
        self.assertIsNone(checkmeta.bomTest("false", "abc", {}))

    def test_readPatterns_acceptsValidInput(self):
        data = [
            "syntax: glob",
            "",
            "checks: encoding(ascii)",
            "*"
        ]
        patterns = checkmeta.readPatterns(UniMock(), data, decodeChecks=False)
        self.assertEqual(["relglob:*"], patterns.keys())
        self.assertEqual(["encoding(ascii)"], patterns["relglob:*"])

    def test_readPatterns_warnsAboutInvalidSyntax(self):
        data = ["syntax: invalid"]
        ui = UniMock()
        checkmeta.readPatterns(ui, data)
        self.assertEqual(1, ui.numCalls("warn"))

    def test_readPatterns_continuesAfterInvalidSyntax(self):
        data = [
            "syntax: invalid",
            "checks: enc(ascii)",
            "*"
        ]
        patterns = checkmeta.readPatterns(UniMock(), data)
        # default syntax is relre
        self.assertEqual(["relre:*"], patterns.keys())

    def test_readPatternFiles_works(self):
        data = [
            "checks: enc(ascii)",
            "*"
        ]
        patternsDirect = checkmeta.readPatterns(UniMock(), data)
        with NamedTemporaryFile() as f:
            f.write("\n".join(data))
            f.flush()
            patternsFile = checkmeta.readPatternFiles(UniMock(), [f.name])

        self.assertEqual(patternsDirect.keys(), patternsFile.keys())

if __name__ == '__main__':
    unittest.main()
