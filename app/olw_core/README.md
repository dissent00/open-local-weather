# olw_core

The deterministic forecast core — extraction, scoring, verification, ground-AQI
staleness, day-over-day comparison, the Open-Meteo fetch layer, and the three
LLM providers — as a pure Dart package.

**Licensed Apache-2.0, not AGPLv3 like the rest of this repository.** See
[Licensing](#licensing) below for why.

## What it is

A port of the Python implementation in `src/openlocalweather/`, held to that
implementation's exact behaviour by the shared vectors in
[`spec/`](../../spec/README.md). Every function here has a counterpart there,
and `test/vectors_test.dart` fails if the two ever disagree.

Deliberately **pure Dart with no Flutter dependency**: this is the
credibility-critical math, and it shouldn't need an emulator or a UI toolchain
to verify. `dart test` runs the whole suite in under a second, on a plain Dart
container in CI, and the package stays reusable by anything — an app, a CLI, a
server.

## Licensing

The Python pipeline in this repository is AGPLv3, so that anyone running a
modified version as a public forecasting service has to share their
improvements back. That's the right licence for the thing people fork and host.

This package is **Apache-2.0** instead, for two reasons:

1. **It protects nothing to restrict it.** The forecast math is already
   published in Python under AGPL. Anyone can port it — that's precisely what
   this package is. Copyleft here would inconvenience honest reuse without
   guarding anything that isn't already open.
2. **It keeps the core genuinely shared.** The maintainer's mobile app is
   closed-source, and a permissive licence here means the same core can be
   used by that app, by this repository, and by anyone else, without the
   licence itself forcing the code to fork into divergent copies. Improvements
   made in either direction can flow back here.

Apache-2.0 rather than MIT for the express patent grant and the trademark
clause, both of which matter more once commercial use is in the picture.

Practically: you may use this package in proprietary software. If you modify
and publicly host the *Python pipeline*, AGPL still applies to that.

## Running the tests

```bash
dart pub get
dart test
```

The vector tests read `../../spec/vectors/*.json`, so run them from within this
directory with the repository checked out.
