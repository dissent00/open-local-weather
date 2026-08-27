#!/usr/bin/env node
/**
 * Manual verification harness for AppsScriptMailer.gs.
 *
 * Apps Script has no local test runner, so this mocks the small set of
 * Apps Script globals the mailer depends on (PropertiesService,
 * UrlFetchApp, MailApp, Logger, Utilities, ScriptApp) and exercises the
 * real script source against fixtures/sample_entry.json — a real,
 * unmodified forecast entry pulled from a live pipeline run, not a
 * synthetic stand-in.
 *
 * NOT part of the Python test suite or CI (this script doesn't run via
 * GitHub Actions at all — see mailer/README.md for why). Run manually
 * before deploying a change to AppsScriptMailer.gs:
 *
 *   node mailer/test_mailer.js
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');

let scriptProps = {
  SUBSCRIBER_EMAILS: 'alice@example.com, bob@example.com',
  GITHUB_REPO: 'dissent00/open-local-weather',
  GITHUB_BRANCH: 'main',
  LOCATION_NAME: 'Kisumu, Kenya',
  TIMEZONE: 'Africa/Nairobi',
  PUBLIC_URL: 'https://dissent00.github.io/open-local-weather/',
};

global.PropertiesService = {
  getScriptProperties: () => ({
    getProperty: (key) => scriptProps[key] || null,
    setProperty: (key, value) => { scriptProps[key] = value; },
  }),
};

const sampleEntryRaw = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'sample_entry.json'), 'utf8'));
// sample_entry.json (a real morning-run entry) has no meta.refreshed_at —
// exactly the "not yet refreshed" state sendForecastEmail() must
// skip on. This is the same entry with a refreshed_at stamped in, for
// exercising the evening happy path — built from the real fixture, not a
// synthetic one, so it stays honest about what a refreshed entry actually
// contains.
const refreshedEntryRaw = { ...sampleEntryRaw, meta: { ...sampleEntryRaw.meta, refreshed_at: '2026-08-11T15:12:00Z' } };

// queuedResponses, if set, lets a test script exactly how many consecutive
// 404s to return before the real fixture "arrives" — used to exercise the
// retry path deterministically. null (the default) means "always 200".
let queuedResponses = null;
let fetchCallCount = 0;
// Which variant of the entry UrlFetchApp serves once it stops 404ing —
// switched per-test so the same mock can exercise both the morning
// (unrefreshed) and evening (refreshed) happy paths.
let servedEntry = sampleEntryRaw;

global.UrlFetchApp = {
  fetch: (url) => {
    fetchCallCount++;
    if (!url.endsWith('2026-08-11.json')) {
      return { getResponseCode: () => 404, getContentText: () => '' };
    }
    if (queuedResponses !== null && queuedResponses > 0) {
      queuedResponses--;
      return { getResponseCode: () => 404, getContentText: () => '' };
    }
    return { getResponseCode: () => 200, getContentText: () => JSON.stringify(servedEntry) };
  },
};

const sentEmails = [];
global.MailApp = { sendEmail: (opts) => sentEmails.push(opts) };
global.Logger = { log: (msg) => console.log('[Logger]', msg) };

let sleepCalls = [];
global.Utilities = {
  formatDate: () => '2026-08-11',
  sleep: (ms) => sleepCalls.push(ms), // no-op — don't actually block the test
};

// Stateful trigger registry, keyed by handler function name -> ARRAY of
// trigger objects (not a single "last config") — needed to actually
// exercise createTriggers()/removeLegacyTriggers() now
// registering several slots per handler, not one, and to prove
// re-running one only ever touches its own handler's triggers, never an
// unrelated handler's.
let triggersByHandler = {};
global.ScriptApp = {
  getProjectTriggers: () => Object.values(triggersByHandler).flat(),
  newTrigger: (fnName) => {
    const cfg = { hour: undefined, minute: undefined, everyMinutes: undefined };
    const builder = {
      timeBased: () => builder,
      atHour: (h) => { cfg.hour = h; return builder; },
      nearMinute: (m) => { cfg.minute = m; return builder; },
      everyDays: () => builder,
      // The mailer now registers a single polling trigger rather than a list
      // of fixed daily slots.
      everyMinutes: (m) => { cfg.everyMinutes = m; return builder; },
      inTimezone: () => builder,
      create: () => {
        const trigger = {
          getHandlerFunction: () => fnName,
          hour: cfg.hour,
          minute: cfg.minute,
          everyMinutes: cfg.everyMinutes,
        };
        if (!triggersByHandler[fnName]) triggersByHandler[fnName] = [];
        triggersByHandler[fnName].push(trigger);
      },
    };
    return builder;
  },
  deleteTrigger: (t) => {
    const fnName = t.getHandlerFunction();
    if (!triggersByHandler[fnName]) return;
    triggersByHandler[fnName] = triggersByHandler[fnName].filter(x => x !== t);
    if (triggersByHandler[fnName].length === 0) delete triggersByHandler[fnName];
  },
};

eval(fs.readFileSync(path.join(__dirname, 'AppsScriptMailer.gs'), 'utf8'));

// Local copy (not a call into the .gs sandbox) purely for building
// expected values in assertions below — kept trivial and independent of
// the script's own AFD_DIVIDER so a bug in the script can't mask itself
// by also breaking the thing checking it.
const AFD_DIVIDER_FOR_TEST = '&&';
// Same value as CHECK_EVERY_MINUTES in AppsScriptMailer.gs — duplicated
// here (not read from the script) so a bug that corrupts the real constant
// can't also corrupt what's checking it.
const EXPECTED_CHECK_MINUTES_FOR_TEST = 30;

function reset() {
  sentEmails.length = 0;
  sleepCalls = [];
  fetchCallCount = 0;
  queuedResponses = null;
  servedEntry = sampleEntryRaw;
  global.Utilities.formatDate = () => '2026-08-11';
  // Idempotency markers are script-managed state, not user config — must
  // not leak "already sent" between otherwise-independent test cases.
  delete scriptProps.SENT_ISSUANCES;
}

// --- Happy path: real fixture data, no retry needed ---
reset();
sendForecastEmail();
assert.strictEqual(sentEmails.length, 2, `expected 2 emails, got ${sentEmails.length}`);
assert.strictEqual(sentEmails[0].to, 'alice@example.com');
// A first issuance is a "Forecast"; a re-issue is an "Update" (below). Both
// carry the issuance time, so a reader can see which one they are holding.
assert.ok(sentEmails[0].subject.startsWith('[Kisumu, Kenya Weather] Forecast — 2026-08-11'), `subject line wrong: ${sentEmails[0].subject}`);

// Plain-text (AFD-style) body — unchanged shape, disclaimer now at the bottom
assert.ok(sentEmails[0].body, 'plain-text body missing');
assert.ok(sentEmails[0].body.includes('.DISCLAIMER...'), 'AFD-style disclaimer header missing from plain-text body');
assert.ok(/not[\s\S]*?for life-safety decisions/i.test(sentEmails[0].body), 'beta/not-for-life-safety disclaimer text missing');
assert.ok(sentEmails[0].body.includes('https://dissent00.github.io/open-local-weather/'), 'public URL missing from plain-text body');
assert.ok(sentEmails[0].body.includes(AFD_DIVIDER_FOR_TEST), '"&&" AFD-style divider missing from plain-text body');
assert.ok(!sentEmails[0].body.includes('**'), 'markdown bold markers should be stripped, not left as literal "**"');
sentEmails[0].body.split('\n').forEach(line => {
  assert.ok(line.length <= 78, `plain-text body line exceeds 78 columns: "${line}"`);
});
assert.ok(
  sentEmails[0].body.indexOf('.DISCLAIMER...') > sentEmails[0].body.indexOf('.ABOUT THIS PRODUCT...'),
  'disclaimer should appear near the bottom of the plain-text body, after the About/links section, not near the top'
);

// HTML body — now site-styled (stat-grid, real headings), not a
// monospace <pre> copy of the plain text
assert.ok(sentEmails[0].htmlBody.includes('<table'), 'HTML body should include the site-styled stat-grid table');
assert.ok(sentEmails[0].htmlBody.includes('High / Low'), 'HTML stat grid should include a High/Low stat');
assert.ok(sentEmails[0].htmlBody.includes('Rain'), 'HTML stat grid should include a Rain stat');
assert.ok(sentEmails[0].htmlBody.includes('<h2'), 'HTML body should render narrative "## Heading" markdown as real <h2> tags, not AFD dot-headers');
assert.ok(sentEmails[0].htmlBody.includes('<a href="https://dissent00.github.io/open-local-weather/"'), 'HTML body should link to the public site as a real <a> tag');
assert.ok(/not.*official government product/i.test(sentEmails[0].htmlBody), 'HTML body should carry the same disclaimer as the plain-text version');
assert.ok(
  sentEmails[0].htmlBody.indexOf('not an official government product') > sentEmails[0].htmlBody.indexOf('<h2'),
  'HTML disclaimer should appear near the bottom, after the narrative, not near the top'
);
assert.strictEqual(sleepCalls.length, 0, 'a check must never sleep — see below');
console.log('PASS: happy path — sent', sentEmails.length, 'emails');

// --- No entry yet: skip quietly, do NOT sleep ---
//
// The in-check retry was removed deliberately when the mailer moved to
// polling. With a check every 30 minutes the NEXT CHECK is the retry, at no
// execution cost — whereas sleeping ~3 minutes on every check before the
// day's first forecast would burn roughly 36 minutes of a consumer account's
// 90-minute DAILY runtime quota waiting for a file that is not due yet.
reset();
global.Utilities.formatDate = () => '2099-01-01'; // never matches the fixture URL
sendForecastEmail();
assert.strictEqual(sentEmails.length, 0, 'should not send when there is no entry');
assert.strictEqual(sleepCalls.length, 0, `a check must not sleep; got ${sleepCalls.length} sleep(s)`);
assert.strictEqual(fetchCallCount, 1, `expected exactly 1 fetch per check, got ${fetchCallCount}`);
console.log('PASS: no entry yet — skipped in one fetch, no sleeping');

// --- The same issuance is never sent twice ---
//
// This is what makes frequent polling safe: 48 checks a day must produce one
// email per issuance, not 48.
reset();
sendForecastEmail();
const firstRound = sentEmails.length;
sendForecastEmail();
sendForecastEmail();
assert.strictEqual(sentEmails.length, firstRound, 'a repeated check must not resend the same issuance');
console.log('PASS: repeated checks send nothing new');

// --- A NEW issuance of the same day does send ---
//
// Keyed on the entry's own timestamp rather than on a per-day flag, so a
// second or fifth run of the day is delivered rather than suppressed.
reset();
sendForecastEmail();
const beforeReissue = sentEmails.length;
servedEntry = refreshedEntryRaw;
sendForecastEmail();
assert.ok(sentEmails.length > beforeReissue, 'a re-issued forecast must be sent, not suppressed as a duplicate');
console.log('PASS: a later issuance of the same day is delivered');

// --- No subscribers configured — should not even attempt a fetch ---
reset();
scriptProps.SUBSCRIBER_EMAILS = '';
sendForecastEmail();
assert.strictEqual(sentEmails.length, 0, 'should not send when no subscribers are configured');
assert.strictEqual(fetchCallCount, 0, 'should not fetch at all when there are no subscribers');
console.log('PASS: no subscribers configured, nothing sent, no fetch attempted');

scriptProps.SUBSCRIBER_EMAILS = 'alice@example.com';

// --- Evening happy path: entry has meta.refreshed_at set ---
reset();
servedEntry = refreshedEntryRaw;
sendForecastEmail();
assert.strictEqual(sentEmails.length, 1, `expected 1 email, got ${sentEmails.length}`);
assert.ok(sentEmails[0].subject.startsWith('[Kisumu, Kenya Weather] Update — 2026-08-11'), `re-issue subject line wrong: ${sentEmails[0].subject}`);
assert.ok(/Update —/.test(sentEmails[0].body), 'a re-issue body should carry an Update run label with its time');
// Time-agnostic wording: a re-issue can now happen at any hour, so the note
// must not claim to be "an evening refresh" or credit "the morning run".
assert.ok(/updates the forecast already issued today/i.test(sentEmails[0].body), 're-issue body should explain it updates an earlier issuance');
assert.ok(/first run counts toward model verification/i.test(sentEmails[0].body), "re-issue body should say the day's FIRST run is the accuracy-tracked one");
assert.ok(!/evening refresh|morning run/i.test(sentEmails[0].body), 'the note must not assume a morning/evening split');
console.log('PASS: re-issue happy path — sent', sentEmails.length, 'email(s)');

// --- An UNrefreshed entry is still the day's first issuance and IS sent ---
//
// The old design waited for meta.refreshed_at before the evening send, because
// it had to distinguish "the morning entry" from "the refreshed one". Keying
// on the issuance timestamp makes that unnecessary: an entry with no
// refreshed_at is simply the first issuance, and the first issuance is the one
// most worth delivering.
reset();
servedEntry = sampleEntryRaw; // real fixture — no meta.refreshed_at
sendForecastEmail();
// One subscriber by this point — an earlier case narrowed the list.
assert.strictEqual(sentEmails.length, 1, 'the first issuance of a day must be sent');
assert.ok(sentEmails[0].subject.includes('Forecast —'), 'a first issuance is a Forecast, not an Update');
assert.strictEqual(sleepCalls.length, 0, 'still no sleeping');
console.log('PASS: an unrefreshed entry is delivered as the first issuance');

// --- createTriggers registers exactly one polling trigger ---
//
// One trigger, not a slot list. Re-running it must replace rather than
// accumulate, or every re-deploy would multiply the number of checks.
reset();
triggersByHandler = {};
createTriggers();
createTriggers();
assert.strictEqual(
  (triggersByHandler.sendForecastEmail || []).length,
  1,
  `expected exactly 1 polling trigger after two calls, got ${(triggersByHandler.sendForecastEmail || []).length}`
);
assert.strictEqual(
  triggersByHandler.sendForecastEmail[0].everyMinutes,
  EXPECTED_CHECK_MINUTES_FOR_TEST,
  'polling interval does not match CHECK_EVERY_MINUTES'
);
console.log('PASS: one polling trigger registered, re-running replaces rather than accumulates');

// --- removeLegacyTriggers clears the old handlers and nothing else ---
//
// Apps Script does not delete a trigger when its handler disappears; it keeps
// firing, failing, and emailing the owner about it. An upgrade that silently
// starts mailing someone error reports would be a poor way to ship a
// simplification.
// Stand-ins shaped like real triggers — deleteTrigger asks each for its
// handler name, so a bare object would throw rather than assert.
triggersByHandler.sendDailyForecastEmail = [
  { getHandlerFunction: () => 'sendDailyForecastEmail', hour: 6, minute: 20 },
];
triggersByHandler.sendEveningRefreshEmail = [
  { getHandlerFunction: () => 'sendEveningRefreshEmail', hour: 18, minute: 20 },
];
removeLegacyTriggers();
assert.strictEqual((triggersByHandler.sendDailyForecastEmail || []).length, 0, 'legacy morning trigger should be removed');
assert.strictEqual((triggersByHandler.sendEveningRefreshEmail || []).length, 0, 'legacy evening trigger should be removed');
assert.strictEqual((triggersByHandler.sendForecastEmail || []).length, 1, 'removeLegacyTriggers must not touch the live polling trigger');
console.log('PASS: legacy triggers removed, the live one left alone');

// --- Idempotency: a second CHECK must not resend the same issuance.
//
// This is what makes polling every 30 minutes safe: 48 checks a day produce
// one email per issuance, not 48. Unlike the old design the second check DOES
// re-fetch — it has to, since only the fetched entry can say whether a newer
// issuance exists — but it must not send. ---
reset(); // SUBSCRIBER_EMAILS is 'alice@example.com' only at this point in the file (narrowed earlier) — 1 recipient per send, not 2
sendForecastEmail(); // first slot of the day — real send
assert.strictEqual(sentEmails.length, 1, 'the first check of the day should send normally');
sendForecastEmail(); // a later check, same issuance
assert.strictEqual(sentEmails.length, 1, 'a repeat check must not send a duplicate email');
console.log('PASS: idempotency — a repeat check is a no-op, not a duplicate send');

// --- Idempotency is keyed by date, not just presence — a marker left
// over from a PRIOR day must not suppress today's send ---
reset();
scriptProps.SENT_ISSUANCES = '2026-08-10'; // yesterday's marker, today (per the mock) is 2026-08-11
sendForecastEmail();
assert.strictEqual(sentEmails.length, 1, 'a marker from a prior day must not suppress today\'s send');
console.log('PASS: morning idempotency marker is keyed by date — a stale marker from a prior day does not suppress today');

// --- Same idempotency guarantee for the evening send ---
reset();
servedEntry = refreshedEntryRaw;
sendForecastEmail();
assert.strictEqual(sentEmails.length, 1, 'first evening slot of the day should send normally');
sendForecastEmail();
assert.strictEqual(sentEmails.length, 1, 'a second evening slot the same day must not send a duplicate email');
console.log('PASS: evening idempotency — a same-day repeat slot is a no-op, not a duplicate send');

// --- convertMarkdownToHtml: direct tests, since the real fixture's
// narrative doesn't happen to contain **bold** text or bullet lists, so
// exercising this only through a full sendForecastEmail() call
// wouldn't actually prove the conversion logic works ---
assert.ok(
  convertMarkdownToHtml('## Overview\nRain **likely** today.').includes('<h2'),
  '"## Heading" should become a real <h2> tag'
);
assert.ok(
  convertMarkdownToHtml('### Sub').includes('<h3'),
  '"### Subheading" should become a real <h3> tag'
);
assert.strictEqual(
  convertMarkdownToHtml('Rain **likely** today.'),
  '<p style="margin: 0.6em 0; line-height: 1.6;">Rain <strong>likely</strong> today.</p>',
  '**bold** markdown should become <strong>, with no literal ** left over'
);
assert.ok(
  convertMarkdownToHtml('- one\n- two').includes('<ul') && convertMarkdownToHtml('- one\n- two').includes('<li'),
  '"- " bullet lines should become a real <ul><li> list'
);
assert.ok(
  convertMarkdownToHtml('5 < 10 & 10 > 5').includes('&lt;') && convertMarkdownToHtml('5 < 10 & 10 > 5').includes('&gt;'),
  'literal <, >, & characters in narrative text must be HTML-escaped, not passed through raw'
);
console.log('PASS: convertMarkdownToHtml handles headings, bold, lists, and HTML-escaping correctly');

// --- sanitizePublicUrl: the real bug this was added for, plus the
// defensive cases around it ---
assert.strictEqual(sanitizePublicUrl(null), '', 'unset PUBLIC_URL should sanitize to empty (not configured)');
assert.strictEqual(sanitizePublicUrl(''), '', 'empty PUBLIC_URL should sanitize to empty');
assert.strictEqual(
  sanitizePublicUrl('https://dissent00.github.io/open-local-weather/'),
  'https://dissent00.github.io/open-local-weather/',
  'a correctly-entered URL must pass through unchanged'
);
// The actual reported bug: a Script Property value of exactly `" "`
// rendered as a literal '  " "' line in a real subscriber email instead
// of either a working link or being omitted.
assert.strictEqual(sanitizePublicUrl('" "'), '', 'a quote-space-quote garbage value must sanitize to empty, not render literally');
assert.strictEqual(
  sanitizePublicUrl('"https://dissent00.github.io/open-local-weather/"'),
  'https://dissent00.github.io/open-local-weather/',
  'a value accidentally wrapped in straight quotes should be unwrapped, not rejected'
);
assert.strictEqual(
  sanitizePublicUrl('  https://dissent00.github.io/open-local-weather/  '),
  'https://dissent00.github.io/open-local-weather/',
  'surrounding whitespace should be trimmed'
);
assert.strictEqual(sanitizePublicUrl('not a url'), '', 'text that is not an http(s) URL must sanitize to empty, never sent as-is');
console.log('PASS: sanitizePublicUrl handles the reported bug and related garbage values');

// --- getConfig() actually wires sanitizePublicUrl in, end to end ---
reset();
scriptProps.PUBLIC_URL = '" "';
let config = getConfig();
assert.strictEqual(config.publicUrl, '', 'getConfig() must sanitize a garbage PUBLIC_URL, not pass it through raw');
scriptProps.PUBLIC_URL = 'https://dissent00.github.io/open-local-weather/';
config = getConfig();
assert.strictEqual(config.publicUrl, 'https://dissent00.github.io/open-local-weather/', 'getConfig() must still return a valid PUBLIC_URL correctly');
console.log('PASS: getConfig() sanitizes PUBLIC_URL end to end');

// --- Full regression: the exact broken email from the bug report must
// no longer be possible — a garbage PUBLIC_URL omits the link section
// entirely instead of rendering '  " "' ---
reset();
scriptProps.PUBLIC_URL = '" "';
sendForecastEmail();
assert.ok(!sentEmails[0].body.includes('" "'), 'plain-text body must never contain the literal broken "  \\" \\"" line again');
assert.ok(!sentEmails[0].body.includes('Past forecasts and the full accuracy record'), 'the publicUrl-gated link lines should be omitted entirely when PUBLIC_URL is unusable, not rendered broken');
scriptProps.PUBLIC_URL = 'https://dissent00.github.io/open-local-weather/'; // restore for any future tests
console.log('PASS: the reported broken-link bug no longer reproduces');

// --- The re-issue note belongs in the Detailed Discussion, not above the
// forecast. It is a note about the accuracy record; on an evening update it
// used to be the first thing a reader met, ahead of anything about the sky. ---
reset();
servedEntry = refreshedEntryRaw;
sendForecastEmail();
{
  const text = sentEmails[0].body;
  const html = sentEmails[0].htmlBody;
  const note = 'accuracy-tracking record';

  assert.ok(text.includes(note), 'a re-issue must still carry the note somewhere');
  assert.ok(
    text.indexOf(note) > text.indexOf('.DETAILED DISCUSSION...'),
    'plain-text note must sit inside the Detailed Discussion, not above the forecast'
  );
  assert.ok(
    text.indexOf(note) > text.indexOf('.OVERVIEW...'),
    'and specifically below the Overview, which is what the reader came for'
  );

  assert.ok(html.includes(note), 'the HTML re-issue note must survive too');
  assert.ok(
    html.indexOf(note) > html.indexOf('Detailed Discussion'),
    'HTML note must follow the Detailed Discussion heading'
  );
}
console.log('PASS: the re-issue note sits in the Detailed Discussion');

// --- ...and a renamed heading must not lose it. The heading is written by
// the model, so the fallback is the difference between a re-issue that says
// so at the bottom and one that reads exactly like a first run. ---
reset();
servedEntry = {
  ...refreshedEntryRaw,
  narrative_markdown: (refreshedEntryRaw.narrative_markdown || '')
    .replace(/## Detailed Discussion/g, '## Forecaster Notes'),
};
sendForecastEmail();
assert.ok(
  sentEmails[0].body.includes('accuracy-tracking record'),
  'plain-text note must fall back below the narrative when the heading is renamed'
);
assert.ok(
  sentEmails[0].htmlBody.includes('accuracy-tracking record'),
  'HTML note must fall back too'
);
console.log('PASS: a renamed discussion heading does not lose the re-issue note');

console.log('\nALL MAILER HARNESS CHECKS PASSED');
