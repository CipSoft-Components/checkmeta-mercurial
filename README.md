Mercurial CheckMeta Extension
=============================

Checkmeta is an extension for mercurial that allows "meta" attributes to be set on files and verifies that the file
adheres to those attributes.

For example you can specify that all cpp files shall be ascii encoded and the extension will prevent any commit with
non-ascii characters in cpp files.
You can also specify attributes as "mandatory" and the extension will disallow commits if one of the modified files
doesn't have all mandatory attributes set.

Available Attributes
--------------------

Right now the following attributes are supported:
* encoding - specifies file encoding for the file(s). The name has to correspond to one of pythons standard encodings
  (see [here](https://docs.python.org/2.4/lib/standard-encodings.html)) or "binary".
* mimetype - specifies a mime-type for the file(s). The only verification currently done for mime-types is that a
  text/something file can't contain non-printable characters (apart from whitespaces)
* bom - specifies if the file(s) must/can't have a unicode byte order mark.
* bmp - specifies that the file(s) can only contain characters from the unicode basic multilingual plane

Installation
------------

CheckMeta is currently not available through pip.

download checkmeta.py from here, place it somewhere, then add the following to your mercurial configuration:

    [extensions]
    checkmeta=/path/to/checkmeta/checkmeta.py

Note: Other files in this repository are not required to use checkmeta.

Configuration
-------------

The extension currently has two configuration parameters:

    [checkmeta]
    pattern_files=
    mandatory=

"pattern_files" specifies one or more files containing file patterns assigning attributes to files/groups of files (see
below). Defaults to .hgmeta (placed in the root of the repository)
"mandatory" specifies attributes that have to be set for all files in the repository.

Usage
-----

To set meta attributes on files you add checks and patterns to one of the pattern files (by default: .hgmeta inside the
repository).
The format for this file is derived from the .hgignore file format so it uses the same support for glob and regex
syntax.
Checks are specified before the file patterns on a separate line in a function-call like syntax:

    checks: encoding(utf-8) bom(false)

If PyQt4 is available on the system one can also run

    hg metaconfig

to invoke a graphical interface to make configuring this file easier.

Example
-------

An example .hgmeta file could look like this:

    syntax: glob

    checks: encoding(ascii) mimetype(text/plain)
    *.h
    *.cpp
    .hgmeta
    checks: encoding(utf-8) mimetype(text/x-java-source) bom(true)
    relre:.*\\.java

Line by line:
* first, the default syntax is set to globbing
* All .h and .cpp as well as the .hgmeta file itself are set to be ascii text files.
* All .java files are set to be utf-8 text files with byte order mark
