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

global.UrlFetchApp = {
  fetch: (url) => {
    if (url.endsWith('2026-08-11.json')) {
      return { getResponseCode: () => 200, getContentText: () => sampleEntry };
    }
    return { getResponseCode: () => 404, getContentText: () => '' };
  },
};

const sentEmails = [];
global.MailApp = { sendEmail: (opts) => sentEmails.push(opts) };
global.Logger = { log: (msg) => console.log('[Logger]', msg) };
global.Utilities = { formatDate: () => '2026-08-11' };

let registeredTriggers = [];
global.ScriptApp = {
  getProjectTriggers: () => [],
  newTrigger: (fnName) => {
    const builder = {
      timeBased: () => builder,
      atHour: () => builder,
      everyDays: () => builder,
      inTimezone: () => builder,
      create: () => registeredTriggers.push(fnName),
    };
    return builder;
  },
  deleteTrigger: () => {},
};

eval(fs.readFileSync(path.join(__dirname, 'AppsScriptMailer.gs'), 'utf8'));

// --- Happy path: real fixture data ---
sendDailyForecastEmail();
assert.strictEqual(sentEmails.length, 2, `expected 2 emails, got ${sentEmails.length}`);
assert.strictEqual(sentEmails[0].to, 'alice@example.com');
assert.ok(sentEmails[0].subject.includes('[Kisumu, Kenya Weather] Daily Forecast — 2026-08-11'), 'subject line wrong');
assert.ok(sentEmails[0].htmlBody.includes('<h3'), 'narrative markdown was not converted to HTML');
assert.ok(sentEmails[0].htmlBody.includes('View on the web'), 'public URL footer missing');
console.log('PASS: happy path — sent', sentEmails.length, 'emails');

// --- Missing forecast entry (404) skips gracefully, does not throw ---
sentEmails.length = 0;
global.Utilities.formatDate = () => '2099-01-01';
sendDailyForecastEmail();
assert.strictEqual(sentEmails.length, 0, 'should not send when the forecast entry is missing');
console.log('PASS: missing entry skipped gracefully');

// --- No subscribers configured ---
sentEmails.length = 0;
scriptProps.SUBSCRIBER_EMAILS = '';
global.Utilities.formatDate = () => '2026-08-11';
sendDailyForecastEmail();
assert.strictEqual(sentEmails.length, 0, 'should not send when no subscribers are configured');
console.log('PASS: no subscribers configured, nothing sent');

// --- createDailyTrigger registers the right function ---
scriptProps.SUBSCRIBER_EMAILS = 'alice@example.com';
createDailyTrigger();
assert.ok(registeredTriggers.includes('sendDailyForecastEmail'), 'trigger not registered correctly');
console.log('PASS: daily trigger registered for sendDailyForecastEmail');

console.log('\nALL MAILER HARNESS CHECKS PASSED');
