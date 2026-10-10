/// Python's text primitives, reproduced where a port reads text Python
/// also reads: `str.splitlines()` and `str.strip()` know separators and
/// whitespace that Dart's `LineSplitter` and `trim` do not, and a heading
/// or a cell beside one has to read the same way in both languages.
library;

const Set<int> _lineSeparators = {
  0x0A, 0x0D, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029,
};

const Set<int> _spaces = {
  0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0,
  0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
  0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
};

/// `str.splitlines()`: its separators, "\r\n" as one, and no empty last
/// line after a trailing separator.
List<String> splitLinesLikePython(String text) {
  final lines = <String>[];
  final buffer = StringBuffer();
  final units = text.runes.toList();
  for (var i = 0; i < units.length; i++) {
    final c = units[i];
    if (!_lineSeparators.contains(c)) {
      buffer.writeCharCode(c);
      continue;
    }
    if (c == 0x0D && i + 1 < units.length && units[i + 1] == 0x0A) {
      i++;
    }
    lines.add(buffer.toString());
    buffer.clear();
  }
  if (buffer.isNotEmpty) {
    lines.add(buffer.toString());
  }
  return lines;
}

/// `str.strip()` with no argument: the characters `str.isspace` knows,
/// which is not Dart's `trim` set — Dart strips the BOM and not U+001C to
/// U+001F; Python the reverse.
String stripLikePython(String line) => _strip(line, left: true, right: true);

/// `str.rstrip()`.
String rstripLikePython(String line) => _strip(line, left: false, right: true);

String _strip(String line, {required bool left, required bool right}) {
  final units = line.runes.toList();
  var start = 0;
  var end = units.length;
  while (left && start < end && _spaces.contains(units[start])) {
    start++;
  }
  while (right && end > start && _spaces.contains(units[end - 1])) {
    end--;
  }
  return String.fromCharCodes(units.sublist(start, end));
}

/// `text[:n]` and `len(text)`: Python counts code points, Dart's String
/// counts UTF-16 units.
int lengthLikePython(String text) => text.runes.length;

String headLikePython(String text, int n) => String.fromCharCodes(text.runes.take(n));

/// Python compares strings by code point; Dart's `compareTo` by UTF-16 unit.
int comparePython(String a, String b) {
  final ra = a.runes.toList();
  final rb = b.runes.toList();
  for (var k = 0; k < ra.length && k < rb.length; k++) {
    if (ra[k] != rb[k]) return ra[k] - rb[k];
  }
  return ra.length - rb.length;
}
