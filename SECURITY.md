# Security

## Maintainer

[@dissent00](https://github.com/dissent00) is the security maintainer and
contact for this repository and the `olw_core` package in `app/olw_core/`.

## Reporting a vulnerability

Report privately through GitHub:
[Security → Report a vulnerability](https://github.com/dissent00/open-local-weather/security/advisories/new).
The report is visible only to the maintainer until an advisory is published.

**Do not open a public issue or pull request for a vulnerability.**

Include what is affected, how to reproduce it, and what an attacker gains.

## What to expect

- Acknowledgement within 7 days.
- No fix deadline is promised; this is a one-person project. You will hear
  what is planned and when it ships.
- Credit in the advisory, unless you ask otherwise.

## Scope

Only `main` is supported. In scope:

- the pipeline and its GitHub Actions workflows, including their handling of
  repository secrets;
- the published pages and email, including how model-written text reaches
  them as HTML;
- the Apps Script mailer in `mailer/`;
- `olw_core`.

Out of scope: forecast accuracy, and the third-party services the pipeline
reads from. Report a wrong forecast as an ordinary issue.
