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
// exactly the "not yet refreshed" state sendEveningRefreshEmail() must
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
// exercise createDailyTrigger()/createEveningRefreshTrigger() now
// registering several slots per handler, not one, and to prove
// re-running one only ever touches its own handler's triggers, never an
// unrelated handler's.
let triggersByHandler = {};
global.ScriptApp = {
  getProjectTriggers: () => Object.values(triggersByHandler).flat(),
  newTrigger: (fnName) => {
    const cfg = { hour: undefined, minute: undefined };
    const builder = {
      timeBased: () => builder,
      atHour: (h) => { cfg.hour = h; return builder; },
      nearMinute: (m) => { cfg.minute = m; return builder; },
      everyDays: () => builder,
      inTimezone: () => builder,
      create: () => {
        const trigger = { getHandlerFunction: () => fnName, hour: cfg.hour, minute: cfg.minute };
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

// Local copies (not calls into the .gs sandbox) purely for building
// expected values in assertions below — kept trivial and independent of
// the script's own AFD_DIVIDER/escapeHtml so a bug in the script can't
// mask itself by also breaking the thing checking it.
const AFD_DIVIDER_FOR_TEST = '&&';
function escapeHtmlForTest(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
// Same values as MORNING_TRIGGER_SLOTS/EVENING_TRIGGER_SLOTS in
// AppsScriptMailer.gs — duplicated here (not read from the script) so a
// bug that corrupts the real constant can't also corrupt what's checking
// it. If you change the real slot list, update this to match.
const EXPECTED_MORNING_SLOTS_FOR_TEST = [
  { hour: 6, minute: 20 }, { hour: 6, minute: 50 }, { hour: 7, minute: 20 },
  { hour: 7, minute: 50 }, { hour: 8, minute: 20 },
];
const EXPECTED_EVENING_SLOTS_FOR_TEST = [
  { hour: 18, minute: 20 }, { hour: 18, minute: 50 }, { hour: 19, minute: 20 },
  { hour: 19, minute: 50 }, { hour: 20, minute: 20 },
];

function reset() {
  sentEmails.length = 0;
  sleepCalls = [];
  fetchCallCount = 0;
  queuedResponses = null;
  servedEntry = sampleEntryRaw;
  global.Utilities.formatDate = () => '2026-08-11';
  // Idempotency markers are script-managed state, not user config — must
  // not leak "already sent" between otherwise-independent test cases.
  delete scriptProps.LAST_SENT_MORNING;
  delete scriptProps.LAST_SENT_EVENING;
}

// --- Happy path: real fixture data, no retry needed ---
reset();
sendDailyForecastEmail();
assert.strictEqual(sentEmails.length, 2, `expected 2 emails, got ${sentEmails.length}`);
assert.strictEqual(sentEmails[0].to, 'alice@example.com');
assert.ok(sentEmails[0].subject.includes('[Kisumu, Kenya Weather] Daily Forecast — 2026-08-11'), 'subject line wrong');
assert.ok(sentEmails[0].body, 'plain-text body missing');
assert.ok(sentEmails[0].body.includes('.DISCLAIMER...'), 'AFD-style disclaimer header missing from plain-text body');
assert.ok(/not[\s\S]*?for life-safety decisions/i.test(sentEmails[0].body), 'beta/not-for-life-safety disclaimer text missing');
assert.ok(sentEmails[0].body.includes('https://dissent00.github.io/open-local-weather/'), 'public URL missing from plain-text body');
assert.ok(sentEmails[0].body.includes(AFD_DIVIDER_FOR_TEST), '"&&" AFD-style divider missing from plain-text body');
assert.ok(!sentEmails[0].body.includes('**'), 'markdown bold markers should be stripped, not left as literal "**"');
sentEmails[0].body.split('\n').forEach(line => {
  assert.ok(line.length <= 78, `plain-text body line exceeds 78 columns: "${line}"`);
});
assert.ok(sentEmails[0].htmlBody.includes('<pre'), 'HTML body should be a monospace <pre> block');
assert.ok(sentEmails[0].htmlBody.includes('Courier'), 'HTML body should be styled in Courier for the AFD nod');
assert.strictEqual(sentEmails[0].htmlBody.replace(/<\/?pre[^>]*>/g, '').trim(), escapeHtmlForTest(sentEmails[0].body), 'HTML body should be the plain-text body verbatim (escaped), not an independent rendering');
assert.strictEqual(sleepCalls.length, 0, 'should not sleep/retry when the entry is found on the first try');
console.log('PASS: happy path — sent', sentEmails.length, 'emails, no retries needed');

// --- Entry shows up on the 2nd attempt (retry succeeds) ---
reset();
queuedResponses = 1; // first UrlFetchApp call 404s, second succeeds
sendDailyForecastEmail();
assert.strictEqual(sentEmails.length, 2, 'should send once the entry appears on retry');
assert.strictEqual(sleepCalls.length, 1, `expected exactly 1 sleep before the entry was found, got ${sleepCalls.length}`);
assert.strictEqual(sleepCalls[0], 90 * 1000, 'retry delay should be 90 seconds');
console.log('PASS: entry found on retry (attempt 2/3) — sent after 1 wait');

// --- Entry never shows up — exhausts all retries, skips gracefully ---
reset();
global.Utilities.formatDate = () => '2099-01-01'; // never matches the fixture URL, always 404s
sendDailyForecastEmail();
assert.strictEqual(sentEmails.length, 0, 'should not send when the forecast entry never appears');
assert.strictEqual(sleepCalls.length, 2, `expected exactly 2 sleeps (3 attempts, no wait after the last), got ${sleepCalls.length}`);
assert.strictEqual(fetchCallCount, 3, `expected exactly 3 fetch attempts, got ${fetchCallCount}`);
console.log('PASS: all retries exhausted, skipped gracefully without throwing');

// --- No subscribers configured — should not even attempt a fetch ---
reset();
scriptProps.SUBSCRIBER_EMAILS = '';
sendDailyForecastEmail();
assert.strictEqual(sentEmails.length, 0, 'should not send when no subscribers are configured');
assert.strictEqual(fetchCallCount, 0, 'should not fetch at all when there are no subscribers');
console.log('PASS: no subscribers configured, nothing sent, no fetch attempted');

scriptProps.SUBSCRIBER_EMAILS = 'alice@example.com';

// --- Evening happy path: entry has meta.refreshed_at set ---
reset();
servedEntry = refreshedEntryRaw;
sendEveningRefreshEmail();
assert.strictEqual(sentEmails.length, 1, `expected 1 email, got ${sentEmails.length}`);
assert.ok(sentEmails[0].subject.includes('[Kisumu, Kenya Weather] Evening Update — 2026-08-11'), 'evening subject line wrong');
assert.ok(sentEmails[0].body.includes('Kisumu, Kenya — Evening Update'), 'evening body should carry the "Evening Update" run label');
assert.ok(/evening refresh of the forecast issued earlier today/i.test(sentEmails[0].body), 'evening body should explain it is a refresh, not a fresh accuracy-tracked run');
assert.strictEqual(sleepCalls.length, 0, 'should not sleep/retry when the entry is already refreshed on the first try');
console.log('PASS: evening update happy path — sent', sentEmails.length, 'email(s)');

// --- Evening skip path: entry exists but hasn't been refreshed yet ---
reset();
servedEntry = sampleEntryRaw; // real fixture — no meta.refreshed_at
sendEveningRefreshEmail();
assert.strictEqual(sentEmails.length, 0, 'should not send an evening update when the entry has not actually been refreshed yet');
assert.strictEqual(sleepCalls.length, 2, `expected exactly 2 sleeps (3 attempts, entry never becomes "refreshed"), got ${sleepCalls.length}`);
assert.strictEqual(fetchCallCount, 3, `expected exactly 3 fetch attempts, got ${fetchCallCount}`);
console.log('PASS: evening update skipped gracefully — entry exists but is not refreshed yet');

// --- Evening entry becomes refreshed partway through retries ---
reset();
queuedResponses = 1; // first fetch 404s
servedEntry = refreshedEntryRaw;
sendEveningRefreshEmail();
assert.strictEqual(sentEmails.length, 1, 'should send once a refreshed entry appears on retry');
assert.strictEqual(sleepCalls.length, 1, `expected exactly 1 sleep before the refreshed entry was found, got ${sleepCalls.length}`);
console.log('PASS: evening update found on retry — sent after 1 wait');

// --- createDailyTrigger registers ALL 5 morning slots, and only ever
// touches its own handler's triggers, never an unrelated handler's ---
reset();
triggersByHandler = {};
createEveningRefreshTrigger(); // registered first, so we can prove createDailyTrigger() doesn't wipe it
createDailyTrigger();
assert.strictEqual(
  (triggersByHandler.sendDailyForecastEmail || []).length,
  EXPECTED_MORNING_SLOTS_FOR_TEST.length,
  `expected ${EXPECTED_MORNING_SLOTS_FOR_TEST.length} morning trigger slots, got ${(triggersByHandler.sendDailyForecastEmail || []).length}`
);
assert.deepStrictEqual(
  triggersByHandler.sendDailyForecastEmail.map(t => ({ hour: t.hour, minute: t.minute })),
  EXPECTED_MORNING_SLOTS_FOR_TEST,
  'morning trigger slot times do not match MORNING_TRIGGER_SLOTS'
);
assert.strictEqual(
  (triggersByHandler.sendEveningRefreshEmail || []).length,
  EXPECTED_EVENING_SLOTS_FOR_TEST.length,
  'createDailyTrigger() should not have deleted the unrelated evening trigger slots'
);
console.log(`PASS: ${EXPECTED_MORNING_SLOTS_FOR_TEST.length} morning trigger slots registered for sendDailyForecastEmail, evening slots left untouched`);

// --- createEveningRefreshTrigger registers ALL 5 evening slots, and
// likewise never touches the unrelated morning handler's slots ---
createEveningRefreshTrigger();
assert.strictEqual(
  (triggersByHandler.sendDailyForecastEmail || []).length,
  EXPECTED_MORNING_SLOTS_FOR_TEST.length,
  'createEveningRefreshTrigger() should not have deleted the unrelated morning trigger slots'
);
assert.strictEqual(
  (triggersByHandler.sendEveningRefreshEmail || []).length,
  EXPECTED_EVENING_SLOTS_FOR_TEST.length,
  `expected ${EXPECTED_EVENING_SLOTS_FOR_TEST.length} evening trigger slots, got ${(triggersByHandler.sendEveningRefreshEmail || []).length}`
);
assert.deepStrictEqual(
  triggersByHandler.sendEveningRefreshEmail.map(t => ({ hour: t.hour, minute: t.minute })),
  EXPECTED_EVENING_SLOTS_FOR_TEST,
  'evening trigger slot times do not match EVENING_TRIGGER_SLOTS'
);
console.log(`PASS: ${EXPECTED_EVENING_SLOTS_FOR_TEST.length} evening trigger slots registered for sendEveningRefreshEmail, morning slots left untouched`);

// --- Re-running createDailyTrigger() replaces the slot set cleanly,
// doesn't accumulate duplicates on top of the existing 5 ---
createDailyTrigger();
assert.strictEqual(
  (triggersByHandler.sendDailyForecastEmail || []).length,
  EXPECTED_MORNING_SLOTS_FOR_TEST.length,
  're-running createDailyTrigger() should replace, not accumulate, its own slot set'
);
console.log('PASS: re-running createDailyTrigger() does not accumulate duplicate slots');

// --- Idempotency: a second trigger slot firing the same day must NOT
// send a duplicate email — the real reason multiple slots are safe to
// add at all. Also must not even re-fetch, since the marker check comes
// first. ---
reset(); // SUBSCRIBER_EMAILS is 'alice@example.com' only at this point in the file (narrowed earlier) — 1 recipient per send, not 2
sendDailyForecastEmail(); // first slot of the day — real send
assert.strictEqual(sentEmails.length, 1, 'first slot of the day should send normally');
const fetchCountAfterFirstSend = fetchCallCount;
sendDailyForecastEmail(); // simulates a later trigger slot firing the same day
assert.strictEqual(sentEmails.length, 1, 'a second slot the same day must not send a duplicate email');
assert.strictEqual(fetchCallCount, fetchCountAfterFirstSend, 'a second slot the same day must not even re-fetch — the idempotency check should short-circuit before the GitHub fetch');
console.log('PASS: morning idempotency — a same-day repeat slot is a no-op, not a duplicate send');

// --- Idempotency is keyed by date, not just presence — a marker left
// over from a PRIOR day must not suppress today's send ---
reset();
scriptProps.LAST_SENT_MORNING = '2026-08-10'; // yesterday's marker, today (per the mock) is 2026-08-11
sendDailyForecastEmail();
assert.strictEqual(sentEmails.length, 1, 'a marker from a prior day must not suppress today\'s send');
console.log('PASS: morning idempotency marker is keyed by date — a stale marker from a prior day does not suppress today');

// --- Same idempotency guarantee for the evening send ---
reset();
servedEntry = refreshedEntryRaw;
sendEveningRefreshEmail();
assert.strictEqual(sentEmails.length, 1, 'first evening slot of the day should send normally');
sendEveningRefreshEmail();
assert.strictEqual(sentEmails.length, 1, 'a second evening slot the same day must not send a duplicate email');
console.log('PASS: evening idempotency — a same-day repeat slot is a no-op, not a duplicate send');

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
sendDailyForecastEmail();
assert.ok(!sentEmails[0].body.includes('" "'), 'plain-text body must never contain the literal broken "  \\" \\"" line again');
assert.ok(!sentEmails[0].body.includes('Past forecasts and the full accuracy record'), 'the publicUrl-gated link lines should be omitted entirely when PUBLIC_URL is unusable, not rendered broken');
scriptProps.PUBLIC_URL = 'https://dissent00.github.io/open-local-weather/'; // restore for any future tests
console.log('PASS: the reported broken-link bug no longer reproduces');

console.log('\nALL MAILER HARNESS CHECKS PASSED');
