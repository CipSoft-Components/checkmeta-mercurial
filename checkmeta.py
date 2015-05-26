# checkmeta.py - verify file encoding and other meta attributes on a file
# Copyright 2015 CipSoft GmbH
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

''' enforces configurable file characteristics.  '''

"""
checkmeta uses a file called .hgmeta (by default) in a similar syntax as
.hgignore to specify certain meta attributes for source files like expected
file encoding, mime type, use of bom, ...

The integrated hook tries to verify those attributes and prevents commits and
pushes if they don't check out, i.e. if the encoding for a file is set to
utf-8 and it contains bytes invalid in utf8.

When running as an extension the necessary hooks are added automatically. Also,
the configuration for the extension can specify "mandatory" attributes.
If a file doesn't have the specified attributes it can't be committed/pushed.

Finally the extension adds a command line parameter called "metaconfig" that
will open an editor to configure meta attributes. If PyQt4 is available, this
will be a specialised editor inspired by the tortoise hg editor for .hginore
files.


Example .hgmeta:

    syntax: glob

    checks: encoding(ascii) mimetype(text/plain)
    *.h
    *.cpp
    .hgmeta
    checks: encoding(utf-8) mimetype(text/x-java-source) bom(true)
    relre:.*\\.java

In this example "globbing" syntax is used by default but can be overriden (just
like .hgignore)
C++ files have to contain only ascii characters and be plain text files.
(The mimetype test for text/* will refuse all files containing non-printable
 characters except for line breaks and tabs)
Java files have to be utf-8 text files with a byte order mark
"""

from mercurial.i18n import _
from mercurial import match

import re
import os
import sys
import functools
import codecs
from collections import OrderedDict
from StringIO import StringIO


# region Test invocation
class ExpressionParser(object):
    """
    Parser for matching expressions (glob or regular expressions)
    """
    def __init__(self):
        """
        Constructor
        """
        self.__lineRegEx = re.compile(r'([a-zA-Z0-9]+:)?(.*)')
        self.__syntaxSpecs = set(self.syntaxes().keys() +
                                 self.syntaxes().values())

    @staticmethod
    def syntaxes():
        """
        :return: dictionary mapping "user readable" names to the keys used
        internally.
        :note: The hg match class accepts more expression types than these, but
        hg ignore only uses "relative" expressions as well
        """
        return {
            're': 'relre',
            'regexp': 'relre',
            'glob': 'relglob'
        }

    @staticmethod
    def invertedSyntaxes():
        """
        :return: dictionary mapping internal expression keys to the user
                 readable names
        """
        return dict((v, k) for k, v in ExpressionParser.syntaxes().iteritems())

    def __call__(self, line, syntax):
        """
        :param line: the input line
        :type line: str
        :param syntax: the default syntax to use if no syntax is specified on
                       the line
        :type syntax: str
        :return: the resolved matching expression
        :rtype: str

        parse a pattern-matching expression
        """
        reMatch = self.__lineRegEx.match(line)

        if reMatch.group(0) is None:
            # this shouldn't be possible as the only non-optional part of the
            # re is .*
            raise ValueError("invalid expression")

        if not reMatch.group(1):
            # no syntax override on the line
            return syntax + ":" + line

        syntaxOverride = reMatch.group(1)[:-1]
        if syntaxOverride not in self.__syntaxSpecs:
            raise KeyError("invalid syntax \"{0}\"".format(reMatch.group(1)))

        if syntaxOverride in self.syntaxes():
            return self.syntaxes()[syntaxOverride] + ":" + reMatch.group(2)
        elif syntaxOverride:
            return syntaxOverride + ":" + reMatch.group(2)


class CheckParser(object):
    """
    Parser for "checks:" lines
    """
    def __init__(self, hgUi, decodeChecks=True):
        """
        Constructor
        """
        self.__hgUi = hgUi
        self.__decodeChecks = decodeChecks
        self.__lineRegEx = re.compile(r'\s*([^(]+)\(([^)]*)\)\s*')

    @staticmethod
    def __makeTest(func, *args):
        """
        :param func: the base function
        :param args: the arguments to bind
        :return: new functor

        Create a functor with some arguments already bound
        """
        return functools.partial(func, *args)

    @staticmethod
    def funcMap():
        """
        :return: mapping of names to the check-functions currently supported
        """
        return {
            'encoding': encodingTest,
            'mimetype': mimeTest,
            'bmp': bmpTest,
            'bom': bomTest
        }

    def __call__(self, line):
        """
        :param line: the line to parse
        :type line: str
        :return: list of functors that will run the specified test on a file

        parse a check line and return a list of functors that will execute the
         specified test on a file
        """
        if len(line.strip()) == 0:
            # empty check line
            return []

        funcMap = self.funcMap()

        matches = self.__lineRegEx.findall(line)
        if not matches:
            self.__hgUi.warn(_("ignoring malformed check line:"
                               " {0}\n").format(line))
            return []

        result = []
        for funcMatch in matches:
            if funcMatch[0] not in funcMap:
                self.__hgUi.warn(_("ignoring unknown test function:"
                                   " {0}\n").format(funcMatch[0]))
                continue

            if self.__decodeChecks:
                parameters = [param.strip('\'"')
                              for param in funcMatch[1].split(',')
                              if len(param) > 0]
                result.append((funcMatch[0],
                               self.__makeTest(funcMap[funcMatch[0]],
                                               *parameters)))
            else:
                result.append("{0}({1})".format(*funcMatch))
        return result


def readPatterns(hgUi, iterable, decodeChecks=True):
    """
    :param hgUi: ui object
    :type hgUi: mercurial.ui.ui
    :param iterable: iterable input
    :type iterable: iterable
    :param decodeChecks: determines if checks should be decoded to
             functors (for regular use) or left as strings (for editor)
    :type decodeChecks: bool
    :return: dictionary of patterns to the corresponding tests
    :rtype: OrderedDict
    extract encoding patterns from input
    Pattern and syntax lines should work exactly like with the .hgignore file
    so this contains some code taken from ignore.py
    """
    result = OrderedDict()

    checks = []

    # regex for unescaped comment
    commentRegEx = re.compile(r'((^|[^\\])(\\\\)*)#.*')

    parseExpression = ExpressionParser()
    parseChecks = CheckParser(hgUi, decodeChecks)

    syntaxes = parseExpression.syntaxes()
    syntax = syntaxes['regexp']

    for line in iterable:
        # remove comments, then un-escape hash symbols
        line = commentRegEx.sub(r'\1', line)
        line = line.replace("\\#", "#").rstrip()
        if not line:
            # ignore empty lines
            continue

        if line.startswith("syntax:"):
            try:
                syntax = syntaxes[line[7:].strip()]
            except KeyError, e:
                hgUi.warn(_("ignoring invalid syntax: '{0}'\n").format(e))
        elif line.startswith("checks:"):
            checks = parseChecks(line[7:])
        else:
            result[parseExpression(line, syntax)] = checks

    return result


def readPatternFiles(hgUi, files=None, datas=None):
    """
    :param hgUi: ui object
    :type hgUi: mercurial.ui.ui
    :param files: list of files to read patterns from
    :type files: list of str
    :param datas: list of data blobs to read patterns from
    :type datas: list of str
    :return: dictionary of patterns to the corresponding tests
    :rtype: OrderedDict
    """
    patterns = OrderedDict()
    files = files if files is not None else []
    datas = datas if datas is not None else []
    for fileName in files:
        if not os.path.isfile(fileName):
            continue
        with open(fileName, "r") as f:
            patterns.update(readPatterns(hgUi, f, decodeChecks=True))

    for data in datas:
        patterns.update(readPatterns(hgUi, data.splitlines(),
                                     decodeChecks=True))

    return patterns


def __applyTestSet(hgUi, fileName, tests, fileData):
    """
    :param hgUi: ui object
    :type hgUi: mercurial.ui.ui
    :param fileName: name of the file to test (only used in error messages)
    :type fileName: str
    :param tests: the tests to run on the file
    :type tests: list of functors
    :param fileData: file content to verify
    :type fileData: str
    :return: True if all tests check out ok, False in case of an error

    apply a set of tests to the specified file
    """

    # a map of all tests to their fixed values. These are passed into all tests
    # and each test can treat all other tests as "asserted" since that test
    # would fail if it weren't true.
    # I.e. this means that if there is a mime-type test and an encoding test,
    # the mime-type test can assume the data is encoded in the encoding verified
    # by the encoding test.
    asserted = {
        key: test.args
        for key, test in tests
    }

    checksRun = []
    for name, test in tests:
        error = test(fileData, asserted)
        if error is not None:
            hgUi.warn(_("test {0}{1} failed for {2}: {3}\n")
                      .format(str(test.func.__name__), str(test.args),
                              fileName, error))
            return checksRun, False
        checksRun.append(name)
    return checksRun, True


def runTests(hgUi, fileName, patterns, fileData):
    """
    :param hgUi: ui object
    :type hgUi: mercurial.ui.ui
    :param fileName: name of the file to check (only used for error messages)
    :type fileName: str
    :param patterns: pattern matchers
    :type patterns: dict of match.match to tests
    :param fileData: content of the file to check
    :type fileData: str
    :return:

    find the right test-set, then run those tests
    """
    for matcher, tests in patterns.iteritems():
        if matcher(fileName):
            return __applyTestSet(hgUi, fileName, tests, fileData)

    # no tests specified
    return [], True


def checkhook(ui, repo, node=None, **kwargs):
    """
    :param ui: ui object
    :type ui: mercurial.ui.ui
    :param repo: repository object
    :type repo: mercurial.repo
    :return: False if there was no problem, True if the commit/push should be
             canceled
    :rtype: bool
    """
    configFiles = ui.configlist('checkmeta', 'pattern_files',
                                default=".hgmeta")
    mandatoryChecks = set(ui.configlist('checkmeta', 'mandatory'))

    lastRev = None

    filesToCheck = set()
    if node is None:
        for fileName in repo[node]:
            filesToCheck.add(fileName)
    else:
        lastRev = len(repo) - 1
        # checking a group if revisions
        for rev in xrange(repo[node].rev(), len(repo)):
            for fileName in repo[rev].files():
                filesToCheck.add(fileName)

    patterns = readPatternFiles(ui, datas=[
        repo[lastRev].filectx(fn).data()
        for fn in configFiles
    ])

    matchPatterns = {
        match.match(repo.root, '', [], [pattern]): check
        for pattern, check in patterns.iteritems()
    }

    # for each file affected by the transaction, find the matching pattern and
    #  run all connected tests
    for fileName in filesToCheck:
        checksRun, success = runTests(ui, fileName, matchPatterns,
                                      repo[lastRev].filectx(fileName).data())
        if not success:
            return True

        missingChecks = mandatoryChecks - set(checksRun)
        if len(missingChecks) > 0:
            ui.warn(_("Mandatory checks not run for {0}: {1}\n").format(
                fileName, ", ".join(missingChecks)))
            return True

    return False


def reposetup(ui, repo):
    ui.setconfig('hooks', 'precommit.checkmeta', checkhook, 'checkmeta')
    ui.setconfig('hooks', 'pretxnchangegroup.checkmeta',
                 checkhook, 'checkmeta')
# endregion


# region Actual tests
def encodingTest(encoding, fileContent, asserted):
    """
    :param encoding: expected encoding
    :type encoding: str
    :param fileContent: file content
    :type fileContent: str
    :param asserted: dictionary of information asserted by other tests
    :type asserted: dict
    :return: an error string if the test fails, None otherwise
    test if the specified file is actually encoded in the specified encoding
    """
    try:
        if encoding != "binary":
            codecs.decode(fileContent, encoding)
        # no encoding check for binary files
        return None
    except ValueError, e:
        return _("invalid encoding: {0}").format(e)


def mimeTest(mimeType, fileContent, asserted):
    """
    :param mimeType: expected mime type of the file
    :type mimeType: str
    :param fileContent: file content
    :type fileContent: str
    :param asserted: dictionary of information asserted by other tests
    :type asserted: dict
    :return: an error string if the test fails, None otherwise

    Test that the content of a file matches its mime type
    """

    def isPrintable8bit(char):
        return ord(char) >= 0x20 or char in "\r\n\t"

    def isPrintableUnicode(unichar):
        ordinal = ord(unichar)
        return ordinal in [0x09, 0x0a, 0x0d] or\
            0x20 <= ordinal < 0x7f or\
            ordinal > 0x9f

    if mimeType.startswith("text") and "encoding" in asserted:
        encoding = asserted["encoding"][0].lower()
        if encoding.startswith("utf"):
            if not all(isPrintableUnicode(char)
                       for char in fileContent.decode(encoding)):
                return _("non-printable character in text-file")
        elif encoding == "ascii" or\
                encoding.startswith("latin") or\
                encoding.startswith("iso-8859") or \
                encoding.startswith("iso8859") or \
                encoding.startswith("cp"):
            if not all(isPrintable8bit(char) for char in fileContent):
                return _("non-printable characters in text-file")
        else:
            # can't verify mime-type text with unknown encoding
            return None
    else:
        # no further checks currently
        return None


def bmpTest(fileContent, asserted):
    """
    :param fileContent: file content
    :type fileContent: str
    :param asserted: dictionary of information asserted by other tests
    :type asserted: dict
    :return: an error string if the test fails, None otherwise

    Verify that all characters in the data are within the
    Unicode "Basic Multilingual Plane" (the first 2^16 codepoints)
    """
    def isInBMP(char):
        return ord(char) <= 0xffff

    if "encoding" not in asserted:
        return _("Test for BMP requires encoding to be specified")

    encoding = asserted["encoding"][0].lower()

    if not encoding.startswith("utf"):
        return _("Test for BMP requires an unicode encoding")

    converted = fileContent.decode(encoding)

    if not all(isInBMP(char) for char in converted):
        return _("characters outside BMP")

    return None


def bomTest(bomExpected, fileContent, asserted):
    """
    :param bomExpected: if true the file should have a bom, if false the file
                        shouldn't have one
    :type bomExpected: str
    :param fileContent: file content
    :type fileContent: str
    :param asserted: dictionary of information asserted by other tests
    :type asserted: dict
    :return: an error string if the test fails, None otherwise

    verify that the file has a byte order mark (or doesn't have one).
     Please note that for the "True" case only the correct bom for the
     specified encoding is accepted
    """

    def normalizeEncoding(enc):
        return enc.lower().translate(None, '-_')

    def hasBom(data, boms):
        return any(data[:len(bom)] == bom for bom in boms)

    if bomExpected.lower() in ['true', 'yes', 'y', 'on', '1']:
        if "encoding" not in asserted:
            return _("Test for BOM requires encoding to be specified")

        encoding = normalizeEncoding(asserted["encoding"][0].lower())

        if not encoding.startswith("utf"):
            return _("Test for BOM requires an unicode encoding")

        expectedBoms = {
            "utf8": [codecs.BOM_UTF8],
            "utf8sig": [codecs.BOM_UTF8],
            "utf16": [codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE],
            "utf16be": [codecs.BOM_UTF16_BE],
            "utf16le": [codecs.BOM_UTF16_LE],
            "utf32": [codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE],
            "utf32be": [codecs.BOM_UTF32_BE],
            "utf32le": [codecs.BOM_UTF32_LE]
        }[encoding]

        if not hasBom(fileContent, expectedBoms):
            return _("invalid or missing BOM")
        if encoding.startswith("utf16"):
            # *sigh* the utf16 and utf32 boms share the same first 2 bytes so
            # even if whe have the right first 2 bytes for a utf16 bom it might
            # still turn out to be a utf32 file.
            # The following test is only valid under the assumption that text
            # files don't contain 0x00, 0x00
            if hasBom(fileContent, [codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE]):
                return _("invalid (32-bit) BOM")
    else:
        if hasBom(fileContent, [codecs.BOM_UTF8,
                             codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE,
                             codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE]):
            return _("unexpected BOM")
    return None
# endregion


# region Configuration Dialog
try:
    from PyQt4 import QtGui, uic, QtCore
    # noinspection PyUnresolvedReferences
    from PyQt4.QtCore import Qt

    class CheckConfigurationDialog(QtGui.QDialog):
        """
        Qt Dialog for configuring the check file
        """
        def __init__(self, hgUi, configFiles):
            super(CheckConfigurationDialog, self).__init__(None)

            self.__fileIndex = -1
            self.__hgUi = hgUi
            self.__patterns = None

            """ use separate file for the ui.
                good for development, bad for deployment

            filePath = os.path.dirname(os.path.realpath(__file__))
            uiFile = os.path.join(filePath, "configdialog.ui")
            self.ui = uic.loadUi(uiFile, self)
            """
            uiCode = StringIO(configDialogUi)
            self.ui = uic.loadUi(uiCode, self)

            for fileName in configFiles:
                self.ui.fileBox.addItem(fileName)

            functionMap = CheckParser.funcMap()

            for f in sorted(functionMap.keys()):
                self.ui.checkTypeBox.addItem(f)

            self.ui.buttonBox.rejected.connect(self.close)
            self.ui.fileBox.currentIndexChanged.connect(self.fileChanged)
            self.ui.patternList.currentItemChanged.connect(self.patternSelected)
            self.ui.checkList.currentTextChanged.connect(self.checkSelected)
            self.ui.addCheckBtn.clicked.connect(self.addCheck)
            self.ui.replaceCheckBtn.clicked.connect(self.replaceCheck)
            self.ui.addPatternBtn.clicked.connect(self.addPattern)
            self.ui.replacePatternBtn.clicked.connect(self.replacePattern)
            self.ui.checkTypeBox.currentIndexChanged.connect(
                self.checkTypeChanged)

            def addRemoveAction(widget, handler):
                checkRemove = QtGui.QAction(_("Remove"), self)
                checkRemove.setShortcut(
                    QtGui.QKeySequence(QtCore.Qt.Key_Delete))
                checkRemove.setShortcutContext(
                    QtCore.Qt.WidgetWithChildrenShortcut)
                widget.insertAction(None, checkRemove)
                checkRemove.triggered.connect(handler)

            addRemoveAction(self.ui.checkList, self.delCurrentCheck)
            addRemoveAction(self.ui.patternList, self.delCurrentPattern)

            self.fileChanged()
            self.patternSelected(None, self.ui.patternList.currentItem())

        def closeEvent(self, QCloseEvent):
            # save on close
            self.storeFilePatterns(self.ui.fileBox.currentIndex())

        def fileChanged(self, index=None):
            if index is None:
                index = self.ui.fileBox.currentIndex()
            if self.__fileIndex >= 0:
                self.storeFilePatterns(self.__fileIndex)
            self.loadFilePatterns(index)
            self.__fileIndex = index

        def __updatePattern(self, pattern):
            self.__patterns[pattern] = [
                str(self.ui.checkList.item(row).text())
                for row in range(0, self.ui.checkList.count())
            ]

        def patternSelected(self, itemAfter, itemBefore):
            if itemBefore:
                self.__updatePattern(str(itemBefore.text()))

            if not itemAfter:
                self.ui.checkList.clear()
                self.ui.addCheckBtn.setEnabled(False)
                self.ui.checkList.setEnabled(False)
                self.ui.replacePatternBtn.setEnabled(False)
            else:
                self.ui.addCheckBtn.setEnabled(True)
                self.ui.checkList.setEnabled(True)
                self.ui.replacePatternBtn.setEnabled(True)
                self.ui.checkList.clear()

                syntax, pattern =\
                    str(self.ui.patternList.currentItem().text()).split(":", 2)

                invertedSyntaxes = ExpressionParser.invertedSyntaxes()
                idx = self.ui.syntaxBox.findText(invertedSyntaxes[syntax],
                                                 QtCore.Qt.MatchFixedString)
                if idx >= 0:
                    self.ui.syntaxBox.setCurrentIndex(idx)
                else:
                    self.__hgUi.warn(_("invalid syntax {0}\n").format(syntax))
                self.ui.patternEdit.setText(pattern)

                checks = self.__patterns.get(str(itemAfter.text()), [])
                if checks:
                    for check in checks:
                        self.ui.checkList.addItem(check)

        def checkSelected(self, check):
            checkMatch = re.match(r'([^(]+)\(([^)]*)\)', str(check))
            if checkMatch is None:
                self.ui.checkEdit.setText("")
                self.ui.replaceCheckBtn.setEnabled(False)
            else:
                self.ui.replaceCheckBtn.setEnabled(True)
                idx = self.ui.checkTypeBox.findText(checkMatch.group(1))
                if idx >= 0:
                    self.ui.checkTypeBox.setCurrentIndex(idx)
                self.ui.checkEdit.setText(checkMatch.group(2))

        def delCurrentPattern(self):
            self.ui.patternList.takeItem(self.ui.patternList.currentRow())

        def delCurrentCheck(self):
            self.ui.checkList.takeItem(self.ui.checkList.currentRow())

        def __patternDescriptor(self):
            pattern = str(self.ui.patternEdit.text())
            if pattern:
                syntax = ExpressionParser.syntaxes()[
                    str(self.ui.syntaxBox.currentText()).lower()]
                return syntax + ":" + pattern
            else:
                return None

        def addPattern(self):
            descriptor = self.__patternDescriptor()
            if descriptor:
                self.ui.patternList.addItem(descriptor)
                self.__patterns[descriptor] = []

        def replacePattern(self):
            descriptor = self.__patternDescriptor()
            if descriptor:
                oldDescriptor = str(self.ui.patternList.currentItem().text())
                self.ui.patternList.currentItem().setText(descriptor)
                self.__patterns[descriptor] = self.__patterns[oldDescriptor]
                del(self.__patterns[oldDescriptor])

        def checkTypeChanged(self, index):
            self.ui.checkEdit.setCompleter(
                self.__completer(str(self.ui.checkTypeBox.currentText())))

        def __checkDescriptor(self):
            return "{0}({1})".format(
                str(self.ui.checkTypeBox.currentText()),
                str(self.ui.checkEdit.text())
            )

        def addCheck(self):
            self.ui.checkList.addItem(self.__checkDescriptor())

        def replaceCheck(self):
            self.ui.checkList.currentItem().setText(self.__checkDescriptor())

        def __mostCommonSyntax(self):
            syntaxList = [
                pat.split(':')[0]
                for pat in self.__patterns.keys()
            ]

            if syntaxList:
                syntax = max(set(syntaxList), key=syntaxList.count)

                invertedSyntaxes = ExpressionParser.invertedSyntaxes()

                return invertedSyntaxes[syntax], syntax
            else:
                return None, None

        def storeFilePatterns(self, fileIndex):
            if self.ui.patternList.currentItem():
                self.__updatePattern(
                    str(self.ui.patternList.currentItem().text()))

            with open(str(self.ui.fileBox.itemText(fileIndex)), "w") as f:
                defaultSyntax, defaultSyntaxResolved = self.__mostCommonSyntax()

                if defaultSyntax is not None:
                    f.write("syntax: " + str(defaultSyntax) + "\n")

                lastChecks = None
                for row in range(0, self.ui.patternList.count()):
                    pattern = str(self.ui.patternList.item(row).text())
                    checks = " ".join(self.__patterns[pattern])
                    if checks != lastChecks:
                        f.write("\nchecks: " + checks + "\n")
                        lastChecks = checks
                    if pattern.startswith(defaultSyntaxResolved):
                        pattern = pattern[len(defaultSyntaxResolved) + 1:]
                    f.write(pattern + "\n")

        def loadFilePatterns(self, index):
            self.ui.patternList.clear()
            with open(str(self.ui.fileBox.itemText(index)), "r") as f:
                self.__patterns = readPatterns(self.__hgUi, f,
                                               decodeChecks=False)
            for pat in self.__patterns.keys():
                self.ui.patternList.addItem(pat)

        def __completer(self, func):
            if func in wordLists:
                res = QtGui.QCompleter(wordLists[func], self)
                res.setCaseSensitivity(Qt.CaseInsensitive)
                return res
            else:
                return None


    def meta_config(ui, repo, **opts):
        configFiles = ui.configlist('checkmeta', 'pattern_files',
                                    default=os.path.join(repo.root, ".hgmeta"))

        app = QtGui.QApplication(sys.argv)

        for fileName in configFiles:
            if not os.path.isfile(fileName):
                # noinspection PyTypeChecker
                choice = QtGui.QMessageBox.question(
                    None, _("File missing"), _("The file {0} doesn't exist,"
                                               " create it?").format(fileName),
                    QtGui.QMessageBox.Yes | QtGui.QMessageBox.No
                )
                QtCore.qDebug(str(choice))
                if choice == QtGui.QMessageBox.Yes:
                    # just create the file empty
                    with open(fileName, 'a'):
                        pass
                else:
                    return

        dialog = CheckConfigurationDialog(ui, configFiles)
        dialog.show()
        app.exec_()
except ImportError:
    # no pyqt available, use text editor as fallback
    QtGui = uic = QtCore = None

    def meta_config(ui, repo, **opts):
        """
        :param ui:
        :param repo:
        :param opts:
        :return:
        invoke regular editor to edit the hgmeta file
        """
        configFiles = ui.configlist('checkmeta', 'pattern_files',
                                    default=os.path.join(repo.root, ".hgmeta"))
        for configFile in configFiles:
            if os.name == "nt":
                os.system("start " + configFile)
            elif os.name == "mac":
                os.system("open " + configFile)
            else:
                os.system("{0} {1}".format(os.getenv('EDITOR'), configFile))
# endregion


# region Globals
wordLists = {
    "encoding": [
        "ascii",
        "latin_1",
        "utf-8",
        "utf-8-sig",  # utf-8 with bom
        "utf-16",
        "binary"
    ],
    "bom": [
        "true",
        "false"
    ],
    "mimetype": [
        "application/octet-stream",
        "application/pdf",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.oasis.opendocument.chart",
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.unity",
        "application/x-7z-compressed",
        "application/x-bzip2",
        "application/x-dvi",
        "application/x-latex",
        "application/x-msdownload",
        "application/x-rar-compressed",
        "application/x-sh",
        "application/x-shockwave-flash",
        "application/x-tar",
        "application/x-tex",
        "application/xhtml+xml",
        "application/xml",
        "application/xml-dtd",
        "application/xslt+xml",
        "application/zip",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/x-aac",
        "audio/x-ms-wma",
        "audio/x-wav",
        "image/bmp",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/tiff",
        "image/vnd.adobe.photoshop",
        "image/x-icon",
        "text/csv",
        "text/html",
        "text/plain",
        "text/tab-separated-values",
        "text/x-c",
        "text/x-java-source",
        "text/x-vcalendar",
        "text/yaml",
        "video/mp4",
        "video/mpeg",
        "video/ogg",
        "video/x-m4v"
    ]
}

# endregion


# region Mercurial global variables
cmdtable = {
    "metaconfig": (
        meta_config,
        [('', 'metaconfig', None, _('opens editor for the hgmeta file'))],
        _('hg metaconfig'))
}

testedwith = '3.2.2'
# endregion


# region Ui Description (huge)
configDialogUi = '''<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>CheckDialog</class>
 <widget class="QDialog" name="CheckDialog">
  <property name="geometry">
   <rect>
    <x>0</x>
    <y>0</y>
    <width>739</width>
    <height>498</height>
   </rect>
  </property>
  <property name="windowTitle">
   <string>Check Configuration</string>
  </property>
  <layout class="QVBoxLayout" name="verticalLayout" stretch="0,0,0">
   <item>
    <layout class="QHBoxLayout" name="horizontalLayout_2">
     <item>
      <widget class="QComboBox" name="fileBox"/>
     </item>
     <item>
      <spacer name="horizontalSpacer">
       <property name="orientation">
        <enum>Qt::Horizontal</enum>
       </property>
       <property name="sizeHint" stdset="0">
        <size>
         <width>40</width>
         <height>20</height>
        </size>
       </property>
      </spacer>
     </item>
    </layout>
   </item>
   <item>
    <layout class="QHBoxLayout" name="horizontalLayout" stretch="2,3">
     <item>
      <widget class="QGroupBox" name="groupBox_2">
       <property name="title">
        <string>Patterns</string>
       </property>
       <property name="alignment">
        <set>Qt::AlignLeading|Qt::AlignLeft|Qt::AlignVCenter</set>
       </property>
       <property name="flat">
        <bool>false</bool>
       </property>
       <property name="checkable">
        <bool>false</bool>
       </property>
       <layout class="QVBoxLayout" name="verticalLayout_3">
        <item>
         <layout class="QHBoxLayout" name="horizontalLayout_3">
          <item>
           <widget class="QComboBox" name="syntaxBox">
            <item>
             <property name="text">
              <string>Glob</string>
             </property>
            </item>
            <item>
             <property name="text">
              <string>Regexp</string>
             </property>
            </item>
           </widget>
          </item>
          <item>
           <widget class="QLineEdit" name="patternEdit"/>
          </item>
         </layout>
        </item>
        <item>
         <layout class="QHBoxLayout" name="horizontalLayout_6">
          <item>
           <widget class="QPushButton" name="addPatternBtn">
            <property name="text">
             <string>Add</string>
            </property>
           </widget>
          </item>
          <item>
           <widget class="QPushButton" name="replacePatternBtn">
            <property name="enabled">
             <bool>false</bool>
            </property>
            <property name="text">
             <string>Replace</string>
            </property>
           </widget>
          </item>
         </layout>
        </item>
        <item>
         <widget class="QListWidget" name="patternList">
          <property name="contextMenuPolicy">
           <enum>Qt::ActionsContextMenu</enum>
          </property>
          <property name="dragDropMode">
           <enum>QAbstractItemView::InternalMove</enum>
          </property>
          <property name="defaultDropAction">
           <enum>Qt::TargetMoveAction</enum>
          </property>
         </widget>
        </item>
        <item>
         <widget class="QLabel" name="label_3">
          <property name="text">
           <string>Only the first matching pattern is used.</string>
          </property>
          <property name="wordWrap">
           <bool>true</bool>
          </property>
         </widget>
        </item>
       </layout>
      </widget>
     </item>
     <item>
      <widget class="QGroupBox" name="groupBox">
       <property name="title">
        <string>Checks</string>
       </property>
       <layout class="QVBoxLayout" name="verticalLayout_2">
        <item>
         <layout class="QHBoxLayout" name="horizontalLayout_4">
          <item>
           <widget class="QComboBox" name="checkTypeBox"/>
          </item>
          <item>
           <widget class="QLabel" name="label">
            <property name="text">
             <string>(</string>
            </property>
           </widget>
          </item>
          <item>
           <widget class="QLineEdit" name="checkEdit"/>
          </item>
          <item>
           <widget class="QLabel" name="label_2">
            <property name="text">
             <string>)</string>
            </property>
           </widget>
          </item>
         </layout>
        </item>
        <item>
         <layout class="QHBoxLayout" name="horizontalLayout_5">
          <item>
           <widget class="QPushButton" name="addCheckBtn">
            <property name="text">
             <string>Add</string>
            </property>
           </widget>
          </item>
          <item>
           <widget class="QPushButton" name="replaceCheckBtn">
            <property name="enabled">
             <bool>false</bool>
            </property>
            <property name="text">
             <string>Replace</string>
            </property>
           </widget>
          </item>
         </layout>
        </item>
        <item>
         <widget class="QListWidget" name="checkList">
          <property name="contextMenuPolicy">
           <enum>Qt::ActionsContextMenu</enum>
          </property>
          <property name="dragDropMode">
           <enum>QAbstractItemView::InternalMove</enum>
          </property>
          <property name="defaultDropAction">
           <enum>Qt::TargetMoveAction</enum>
          </property>
         </widget>
        </item>
       </layout>
      </widget>
     </item>
    </layout>
   </item>
   <item>
    <widget class="QDialogButtonBox" name="buttonBox">
     <property name="standardButtons">
      <set>QDialogButtonBox::Close</set>
     </property>
    </widget>
   </item>
  </layout>
 </widget>
 <resources/>
 <connections/>
</ui>
'''
# endregion
