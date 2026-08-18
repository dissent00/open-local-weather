/// Deterministic forecast core for Open Local Weather.
///
/// This is a port of the Python package's correctness-critical logic:
/// prediction extraction, scoring, rolling verification and ground-AQI
/// staleness. Everything here is pure computation — no I/O, no network, no
/// LLM — mirroring the project's first design principle: *all arithmetic in
/// code, never the LLM.*
///
/// The port is held to the Python implementation's exact behaviour by the
/// shared vectors in `spec/vectors/`, exercised by `test/vectors_test.dart`.
/// If you change behaviour here, that test fails — and if the change was
/// intentional, it must be made in Python first and the vectors regenerated.
/// See `spec/README.md` for why that direction matters.
library;

export 'src/aqi.dart';
export 'src/dates.dart';
export 'src/extract.dart';
export 'src/models.dart';
export 'src/scoring.dart';
