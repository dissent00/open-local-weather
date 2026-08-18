// Dev helper: prints the declared schemas so they can be diffed against the
// Python-generated vectors. Not part of the library or its tests.
import 'dart:convert';
import 'package:olw_core/olw_core.dart';

void main() {
  print(jsonEncode({
    'gemini': geminiForecastSchema(),
    'strict': strictForecastSchema(),
  }));
}
