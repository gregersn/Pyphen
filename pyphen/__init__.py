# This file is part of Pyphen
#
# Copyright 2008 - Wilbert Berendsen <info@wilbertberendsen.nl>
# Copyright 2012-2013 - Guillaume Ayoub <guillaume.ayoub@kozea.fr>
#
# This library is free software.  It is released under the
# GPL 2.0+/LGPL 2.1+/MPL 1.1 tri-license.  See COPYING.GPL, COPYING.LGPL and
# COPYING.MPL for more details.
#
# This library is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.

"""

Pyphen
======

Pure Python module to hyphenate text, inspired by Ruby's Text::Hyphen.

"""

import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

VERSION = __version__ = '0.11.0'

__all__ = ('Pyphen', 'LANGUAGES', 'language_fallback')

# cache of per-file HyphDict objects
hdcache: Dict[str, 'HyphDict'] = {}

# precompile some stuff
parse_hex = re.compile(r'\^{2}([0-9a-f]{2})').sub
parse: Callable[[str], Iterable[Tuple[str, str]]
                ] = re.compile(r'(\d?)(\D?)').findall

try:
    from pkg_resources import resource_filename
    dictionaries_root = resource_filename('pyphen', 'dictionaries')
except ImportError:
    dictionaries_root = os.path.join(os.path.dirname(__file__), 'dictionaries')

LANGUAGES: Dict[str, str] = {}
for filename in sorted(os.listdir(dictionaries_root)):
    if filename.endswith('.dic'):
        name = filename[5:-4]
        full_path = os.path.join(dictionaries_root, filename)
        LANGUAGES[name] = full_path
        short_name = name.split('_')[0]
        if short_name not in LANGUAGES:
            LANGUAGES[short_name] = full_path


def language_fallback(language: str):
    """Get a fallback language available in our dictionaries.

    http://www.unicode.org/reports/tr35/#Locale_Inheritance

    We use the normal truncation inheritance. This function needs aliases
    including scripts for languages with multiple regions available.

    """
    parts = language.replace('-', '_').split('_')
    while parts:
        language = '_'.join(parts)
        if language in LANGUAGES:
            return language
        parts.pop()

    raise KeyError(f'Fallback not found for {language}')


class AlternativeParser(object):
    """Parser of nonstandard hyphen pattern alternative.

    The instance returns a special int with data about the current position in
    the pattern when called with an odd value.

    """

    def __init__(self, pattern: str, alternative: str):
        _alternative = alternative.split(',')
        self.change = _alternative[0]
        self.index = int(_alternative[1])
        self.cut = int(_alternative[2])
        if pattern.startswith('.'):
            self.index += 1

    def __call__(self, value: str):
        self.index -= 1
        _value = int(value)
        if _value & 1:
            return DataInt(_value, (self.change, self.index, self.cut))
        else:
            return _value


class DataInt(int):
    data: Optional[Tuple[str, int, int]]
    """``int`` with some other data can be stuck to in a ``data`` attribute."""
    def __new__(cls, value: Union[str, int], data: Optional[Tuple[str, int, int]] = None, reference: Union['DataInt', int, None] = None):
        """Create a new ``DataInt``.

        Call with ``reference=dataint_object`` to use the data from another
        ``DataInt``.

        """
        obj = int.__new__(cls, value)
        if reference and isinstance(reference, DataInt):
            obj.data = reference.data
        else:
            obj.data = data
        return obj


class HyphDict(object):
    """Hyphenation patterns."""

    def __init__(self, filename: str):
        """Read a ``hyph_*.dic`` and parse its patterns.

        :param filename: filename of hyph_*.dic to read

        """
        self.patterns: Dict[str, Any] = {}

        with open(filename, 'rb') as stream:
            # see "man 4 hunspell", iscii-devanagari is not supported by python
            charset = stream.readline().strip().decode('ascii')
            if charset.lower() == 'microsoft-cp1251':
                charset = 'cp1251'
            for pattern in stream:
                pattern = pattern.decode(charset).strip()
                if not pattern or pattern.startswith((
                        '%', '#', 'LEFTHYPHENMIN', 'RIGHTHYPHENMIN',
                        'COMPOUNDLEFTHYPHENMIN', 'COMPOUNDRIGHTHYPHENMIN')):
                    continue

                # replace ^^hh with the real character
                pattern = parse_hex(
                    lambda match: chr(int(match.group(1), 16)), pattern)

                # read nonstandard hyphen alternatives
                if '/' in pattern and '=' in pattern:
                    pattern, alternative = pattern.split('/', 1)
                    factory = AlternativeParser(pattern, alternative)
                else:
                    factory = int

                tags, values = zip(*[
                    (string, factory(i or '0'))
                    for i, string in parse(pattern)])

                # if only zeros, skip this pattern
                if max(values) == 0:
                    continue

                # chop zeros from beginning and end, and store start offset
                start, end = 0, len(values)
                while not values[start]:
                    start += 1
                while not values[end - 1]:
                    end -= 1

                self.patterns[''.join(
                    [t if isinstance(t, str) else "" for t in tags])] = start, values[start:end]

        self.cache: Dict[str, List[DataInt]] = {}
        self.maxlen = max(len(key) for key in self.patterns)

    def positions(self, word: str) -> List[DataInt]:
        """Get a list of positions where the word can be hyphenated.

        :param word: unicode string of the word to hyphenate

        E.g. for the dutch word 'lettergrepen' this method returns ``[3, 6,
        9]``.

        Each position is a ``DataInt`` with a data attribute.

        If the data attribute is not ``None``, it contains a tuple with
        information about nonstandard hyphenation at that point: ``(change,
        index, cut)``.

        change
          a string like ``'ff=f'``, that describes how hyphenation should
          take place.

        index
          where to substitute the change, counting from the current point

        cut
          how many characters to remove while substituting the nonstandard
          hyphenation

        """
        word = word.lower()
        points = self.cache.get(word)
        if points is None:
            pointed_word = '.%s.' % word
            references: List[Any] = [0] * (len(pointed_word) + 1)

            for i in range(len(pointed_word) - 1):
                for j in range(
                        i + 1, min(i + self.maxlen, len(pointed_word)) + 1):
                    pattern = self.patterns.get(pointed_word[i:j])
                    if pattern:
                        offset, values = pattern
                        slice_ = slice(i + offset, i + offset + len(values))
                        references[slice_] = map(
                            max, values, references[slice_])

            points = [
                DataInt(i - 1, reference=reference)
                for i, reference in enumerate(references) if reference % 2]
            self.cache[word] = points
        return points


class Pyphen(object):
    """Hyphenation class, with methods to hyphenate strings in various ways."""

    def __init__(self, filename: Optional[str] = None, lang: Optional[str] = None, left: int = 2, right: int = 2, cache: bool = True):
        """Create an hyphenation instance for given lang or filename.

        :param filename: filename of hyph_*.dic to read
        :param lang: lang of the included dict to use if no filename is given
        :param left: minimum number of characters of the first syllabe
        :param right: minimum number of characters of the last syllabe
        :param cache: if ``True``, use cached copy of the hyphenation patterns

        """
        if not filename and lang:
            filename = LANGUAGES[language_fallback(lang)]
        self.left = left
        self.right = right

        if filename:
            if not cache or filename not in hdcache:
                hdcache[filename] = HyphDict(filename)
            self.hd = hdcache[filename]

    def positions(self, word: str):
        """Get a list of positions where the word can be hyphenated.

        :param word: unicode string of the word to hyphenate

        See also ``HyphDict.positions``. The points that are too far to the
        left or right are removed.

        """
        right = len(word) - self.right
        return [i for i in self.hd.positions(word) if self.left <= i <= right]

    def iterate(self, word: str):
        """Iterate over all hyphenation possibilities, the longest first.

        :param word: unicode string of the word to hyphenate

        """
        for position in reversed(self.positions(word)):
            if position.data:
                # get the nonstandard hyphenation data
                change, index, cut = position.data
                index += position
                if word.isupper():
                    change = change.upper()
                c1, c2 = change.split('=')
                yield word[:index] + c1, c2 + word[index + cut:]
            else:
                yield word[:position], word[position:]

    def wrap(self, word: str, width: int, hyphen: str = '-'):
        """Get the longest possible first part and the last part of a word.

        :param word: unicode string of the word to hyphenate
        :param width: maximum length of the first part
        :param hyphen: unicode string used as hyphen character

        The first part has the hyphen already attached.

        Returns ``None`` if there is no hyphenation point before ``width``, or
        if the word could not be hyphenated.

        """
        width -= len(hyphen)
        for w1, w2 in self.iterate(word):
            if len(w1) <= width:
                return w1 + hyphen, w2

    def inserted(self, word: str, hyphen: str = '-'):
        """Get the word as a string with all the possible hyphens inserted.

        :param word: unicode string of the word to hyphenate
        :param hyphen: unicode string used as hyphen character

        E.g. for the dutch word ``'lettergrepen'``, this method returns the
        unicode string ``'let-ter-gre-pen'``. The hyphen string to use can be
        given as the second parameter, that defaults to ``'-'``.

        """
        word_list = list(word)
        for position in reversed(self.positions(word)):
            if position.data:
                # get the nonstandard hyphenation data
                change, index, cut = position.data
                index += position
                if word.isupper():
                    change = change.upper()
                word_list[index:index + cut] = change.replace('=', hyphen)
            else:
                word_list.insert(position, hyphen)

        return ''.join(word_list)

    __call__ = iterate
