# Contributing

Thanks for considering it. This project exists so that places underserved by
professional meteorology can run their own forecasting, and it gets better
when people who actually live in those places tell it what it's getting wrong.

Please read [Contribution terms](#contribution-terms) before opening a pull
request — this project is dual-licensed and there's a relicensing grant, which
is unusual enough to state plainly rather than bury.

---

## What's most useful

- **Bug reports with evidence.** A forecast that was wrong, with the date, is
  worth more than a general impression. Everything is committed to
  `data/log/`, so a specific day can always be examined after the fact.
- **A `BulletinFetcher` for your country's met service.** The shipped example
  (`fetch/bulletin/kenya_kmd.py`) only works for Kenya. Each one is genuinely
  location-specific work that nobody else can do for you.
- **Fixes to the deterministic core.** Scoring, extraction, verification,
  staleness. These carry the project's credibility.
- **Corrections to the docs**, especially [QUICKSTART.md](QUICKSTART.md). If
  setup was confusing, that's a bug.

## Conventions that aren't obvious

**All arithmetic in code, never the LLM.** The LLM writes prose and makes
judgment calls about disagreeing models. It never computes a statistic. This
has been re-learned the hard way more than once — most recently when it was
asked to compare 29.6°C against 29.5°C and reported "about 1°C cooler." If a
change asks the model to calculate something, it belongs in Python instead.

**Missing data is never zero.** A model with no value at a lead time gets
`None`, never `False`/`0`. UKMO's horizon stops around 7.2 days, so it has no
Day+7 at all — recording that as "no rain expected" would accrue a flattering,
entirely fake accuracy score. `score_prediction` refuses to score a `None`.

**Python is upstream of Dart.** `app/olw_core/` is a port held to the Python
implementation's exact behaviour by the vectors in [`spec/`](spec/README.md).
If you change shared behaviour:

1. change it in Python,
2. run `python spec/export_vectors.py` to regenerate the vectors,
3. update the Dart side until `dart test` passes again.

**Never edit a vector to match code.** The vectors are the contract. A vector
diff you didn't intend is a bug report, not something to accept.

**Both suites must pass.**

```bash
pytest -q                          # from the repository root
cd app/olw_core && dart test       # requires the Dart SDK
```

**Changes that affect forecast content deserve a live check.**
`olw run-daily --dry-run` runs the real fetches and a real LLM call but writes
nothing and emails nobody. Several bugs here were invisible in unit tests and
obvious in one dry run.

**Commit messages explain *why*.** The what is in the diff. Existing history
is the style guide — it is unusually verbose on purpose, because the reasoning
behind a decision is the part that gets lost.

---

## Contribution terms

This repository is dual-licensed (see [Licensing](README.md#licensing)):

- the Python pipeline and docs are **AGPLv3-or-later**
- `app/olw_core/` is **Apache-2.0**

The maintainer also develops a **closed-source mobile app** that shares the
forecast core with this project. Improvements are intended to flow in both
directions.

In these terms, **"the maintainer"** means the owner of this repository —
currently the GitHub account [`dissent00`](https://github.com/dissent00) —
together with their successors and assigns. Defining it by repository
ownership rather than by name means the grant stays unambiguous if
maintainership changes hands, and transfers with the project rather than
being stranded with an individual.

That last point is why the terms below include a relicensing grant. Without
it, an improvement you contribute to the AGPL pipeline could not be ported
into `app/olw_core/` (Apache-2.0) or used in the app, because relicensing your
copyright would need your permission each time. Rather than ask
contribution-by-contribution, the grant is stated up front so you can decide
before you spend any effort.

**You keep your copyright.** This is a licence grant, not an assignment.

### The grant

By submitting a contribution to this project, you agree that:

1. **Ownership.** Each contribution is your original work, or you otherwise
   have the right to submit it under these terms. If your employer has rights
   to work you create, you have permission to contribute, or your employer has
   waived those rights.

2. **Copyright licence.** You grant the project maintainer and all recipients
   a perpetual, worldwide, non-exclusive, royalty-free, irrevocable copyright
   licence to reproduce, prepare derivative works of, publicly display,
   publicly perform, sublicense, and distribute your contribution and such
   derivative works.

3. **Relicensing.** You grant the maintainer (as defined above, including
   successors and assigns) the right to distribute your
   contribution, and derivative works of it, under **AGPLv3-or-later**, under
   **Apache-2.0**, and under **proprietary terms** — including in the
   maintainer's closed-source applications. This right is what allows work
   contributed to the AGPL pipeline to be ported into the permissively
   licensed Dart core and used in the mobile app.

4. **Patent licence.** You grant a perpetual, worldwide, non-exclusive,
   royalty-free, irrevocable patent licence to make, use, sell, offer to sell,
   import and otherwise transfer your contribution, covering only those patent
   claims you can license that are necessarily infringed by your contribution
   alone or by its combination with this project.

5. **No warranty.** You provide your contribution "as is", without warranties
   or conditions of any kind, express or implied.

6. **Your rights are unaffected.** You retain all right, title and interest in
   your contribution, and remain free to use it however you like, including in
   other projects under any licence.

### How to accept

Sign off your commits:

```bash
git commit -s
```

That adds a `Signed-off-by: Your Name <your@email>` line, which for this
project means you agree to the terms above for that contribution. Use your
real name and a real email address.

If you'd rather not agree to the relicensing grant in section 3, that's a
completely reasonable position — please say so in the pull request. Some
contributions (documentation, a met-service bulletin fetcher, anything that
would never be ported to the Dart core) can be accepted under AGPL alone. It
just needs to be an explicit decision rather than an assumption.

### If you have already contributed

These terms are not retroactive. Contributions made before this document
existed were accepted under the licence in effect at the time, and nothing
here changes that.

---

*This document is not legal advice. It follows patterns used by widely
adopted contributor agreements, but if the terms matter to you or your
employer, have someone qualified read them.*
