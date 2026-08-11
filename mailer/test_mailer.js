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
  getScriptProperties: () => ({ getProperty: (key) => scriptProps[key] || null }),
};

const sampleEntry = fs.readFileSync(path.join(__dirname, 'fixtures', 'sample_entry.json'), 'utf8');

// queuedResponses, if set, lets a test script exactly how many consecutive
// 404s to return before the real fixture "arrives" — used to exercise the
// retry path deterministically. null (the default) means "always 200".
let queuedResponses = null;
let fetchCallCount = 0;

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
    return { getResponseCode: () => 200, getContentText: () => sampleEntry };
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

let registeredTriggers = [];
let lastTriggerConfig = {};
global.ScriptApp = {
  getProjectTriggers: () => [],
  newTrigger: (fnName) => {
    const builder = {
      timeBased: () => builder,
      atHour: (h) => { lastTriggerConfig.hour = h; return builder; },
      nearMinute: (m) => { lastTriggerConfig.minute = m; return builder; },
      everyDays: () => builder,
      inTimezone: () => builder,
      create: () => registeredTriggers.push(fnName),
    };
    return builder;
  },
  deleteTrigger: () => {},
};

eval(fs.readFileSync(path.join(__dirname, 'AppsScriptMailer.gs'), 'utf8'));

function reset() {
  sentEmails.length = 0;
  sleepCalls = [];
  fetchCallCount = 0;
  queuedResponses = null;
  global.Utilities.formatDate = () => '2026-08-11';
}

// --- Happy path: real fixture data, no retry needed ---
reset();
sendDailyForecastEmail();
assert.strictEqual(sentEmails.length, 2, `expected 2 emails, got ${sentEmails.length}`);
assert.strictEqual(sentEmails[0].to, 'alice@example.com');
assert.ok(sentEmails[0].subject.includes('[Kisumu, Kenya Weather] Daily Forecast — 2026-08-11'), 'subject line wrong');
assert.ok(sentEmails[0].htmlBody.includes('<h3'), 'narrative markdown was not converted to HTML');
assert.ok(sentEmails[0].htmlBody.includes('View on the web'), 'public URL footer missing');
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

// --- createDailyTrigger registers the right function at ~6:20 ---
scriptProps.SUBSCRIBER_EMAILS = 'alice@example.com';
createDailyTrigger();
assert.ok(registeredTriggers.includes('sendDailyForecastEmail'), 'trigger not registered correctly');
assert.strictEqual(lastTriggerConfig.hour, 6, 'trigger hour should be 6');
assert.strictEqual(lastTriggerConfig.minute, 20, 'trigger minute should be 20');
console.log('PASS: daily trigger registered for sendDailyForecastEmail at ~6:20');

console.log('\nALL MAILER HARNESS CHECKS PASSED');
