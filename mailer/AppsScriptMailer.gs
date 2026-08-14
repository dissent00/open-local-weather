/**
 * Open Local Weather — standalone email mailer (Google Apps Script / MailApp)
 *
 * A companion to the main Python/GitHub Actions pipeline
 * (https://github.com/dissent00/open-local-weather) — NOT part of it, and
 * does not run via GitHub Actions. Runs entirely inside Google's own
 * infrastructure on its own daily trigger, decoupled from the pipeline's
 * cron.
 *
 * WHY THIS EXISTS: sending real subscriber email from a third-party ESP
 * (Brevo, SendGrid, etc.) requires a verified custom domain with DKIM/SPF/
 * DMARC under Google/Yahoo/Microsoft's 2024 bulk-sender rules — a Gmail
 * "from" address can never pass DKIM alignment through a third party,
 * since only Google can sign mail as gmail.com. Sending via raw SMTP
 * (smtp.gmail.com) from GitHub Actions was the next option, but requires a
 * Gmail "app password," which Google doesn't offer on some newer accounts.
 * MailApp sidesteps both problems: it sends through Google's own trusted
 * infrastructure under this script's normal OAuth authorization (the
 * one-time consent screen on first run), no app password needed at all,
 * and it isn't sending from a shared/rotating CI IP the way GitHub
 * Actions -> smtp.gmail.com would have been.
 *
 * Reuses sendEmailBroadcast() from the original KisumuForecastPipeline_v2.gs
 * reference for the per-recipient send loop — see
 * reference/KisumuForecastPipeline_v2.gs in the main repo. The email body
 * itself is NOT ported from that reference: it's rendered as a plain-text,
 * fixed-width layout styled as a nod to NOAA's Area Forecast Discussion
 * (AFD) product — dot-leader ".SECTION..." headers, "&&" dividers, Courier
 * in HTML-capable clients — rather than the original's styled HTML email.
 * See buildEmailPlainText()'s doc comment for the rationale.
 *
 * WHAT IT FETCHES: the day's committed forecast JSON directly from
 * GitHub's raw-content CDN (data/log/YYYY-MM-DD.json) — structured data,
 * not a scrape of the rendered GitHub Pages HTML. Same underlying
 * forecast either way, but immune to any future page-template change.
 *
 * TIMING: each of the morning/evening sends fires from SEVERAL Apps
 * Script trigger slots per day (MORNING_TRIGGER_SLOTS / EVENING_TRIGGER_
 * SLOTS below), not one — this replaced a single-trigger-plus-short-retry
 * design after real evidence that GitHub Actions' own scheduling can be
 * off by a lot more than a few minutes: confirmed via this repo's actual
 * run history, `daily.yml`'s schedule trigger produced zero runs at all
 * on two separate occasions, and on another day all its backup cron slots
 * fired 1h49m-2h13m late as a cluster (see docs-internal/ROADMAP.md items
 * 3 and 11, ops/README.md). A single Apps Script trigger with a ~3-minute
 * retry window (the old design) cannot catch a pipeline that lands two
 * hours late — several trigger slots spread across ~2 hours can. Because
 * multiple slots now call the same send*Email() function on a normal day
 * (not just on a delayed one), each send is guarded by a same-day
 * idempotency check (alreadySentToday(), backed by a Script Property
 * marker) so only the FIRST slot that finds real data actually sends —
 * every later slot that day is a fast, cheap no-op, not a duplicate
 * email. The evening trigger additionally waits for `meta.refreshed_at`
 * to be set, not just for the file to exist — see
 * sendEveningRefreshEmail().
 *
 * ============================== SETUP ==============================
 * 1. script.google.com -> New project -> replace Code.gs's contents with
 *    this file.
 * 2. Project Settings (gear icon) -> Script Properties -> add (enter the
 *    bare value only in the Script Properties VALUE field -- do NOT
 *    include quote marks; the quotes below are this doc's own formatting,
 *    not literal characters to type in. See sanitizePublicUrl()'s doc
 *    comment for the real bug a quoted PUBLIC_URL value caused):
 *      SUBSCRIBER_EMAILS  comma-separated recipient list, e.g.
 *                         a@example.com,b@example.com
 *      GITHUB_REPO        dissent00/open-local-weather (or your fork)
 *      GITHUB_BRANCH      main
 *      LOCATION_NAME      Kisumu, Kenya (match location.yaml's
 *                         primary_place_name)
 *      TIMEZONE           Africa/Nairobi (match location.yaml's timezone)
 *      PUBLIC_URL         https://<owner>.github.io/<repo>/ (linked in
 *                         the email footer; optional, leave blank to omit)
 * 3. Run createDailyTrigger() once from the editor (Run menu) for the
 *    morning send slots. If you also run the pipeline's evening refresh,
 *    also run createEveningRefreshTrigger() once for the evening send
 *    slots — the two are independent, each only ever managing its own
 *    handler's triggers, so running one never disturbs the other. Apps
 *    Script will prompt for authorization the first time — that consent
 *    screen IS the auth mechanism; there's no separate app password step.
 * 4. Done. Each handler now fires from several trigger slots spread
 *    across ~2 hours (see MORNING_TRIGGER_SLOTS / EVENING_TRIGGER_SLOTS)
 *    instead of once — the first slot each day that finds real,
 *    ready data sends the email and marks the day done; every other slot
 *    that day is a fast no-op. If NONE of the slots find ready data by
 *    the last one, that day's email is skipped rather than erroring or
 *    sending stale content.
 * =====================================================================
 */

/** Sanitizes the PUBLIC_URL Script Property. Real bug this fixes: a
 * subscriber email went out with the literal text `" "` where the site
 * link should have been — traced to a stray quoted/garbage value in the
 * PUBLIC_URL Script Property (the setup docs above used to show example
 * values wrapped in quotes as pure documentation styling, which is an
 * easy value to copy-paste literally by mistake; fixed in the docs too).
 * Strips one matching pair of straight or curly quotes if present, trims
 * whitespace, and — the actual safety net, since a fresh mistake could
 * always produce different garbage — falls back to '' (the same as "not
 * configured", which buildEmailPlainText() already omits gracefully)
 * for anything that still doesn't look like a real http(s) URL
 * afterward, logging why, rather than ever interpolating garbage into a
 * real subscriber email again.
 */
function sanitizePublicUrl(raw) {
  if (!raw) return '';
  let value = raw.trim();
  const quotePairs = [['"', '"'], ["'", "'"], ['“', '”'], ['‘', '’']];
  for (const [open, close] of quotePairs) {
    if (value.length >= 2 && value.charAt(0) === open && value.charAt(value.length - 1) === close) {
      value = value.slice(1, -1).trim();
      break;
    }
  }
  if (!/^https?:\/\/\S+/.test(value)) {
    if (value) {
      Logger.log(`PUBLIC_URL Script Property ("${raw}") doesn't look like a valid http(s) URL after sanitizing — treating as not configured (omitted from the email) rather than sending it as-is. Fix it under Project Settings > Script Properties.`);
    }
    return '';
  }
  return value;
}

function getConfig() {
  const props = PropertiesService.getScriptProperties();
  return {
    subscriberEmails: (props.getProperty('SUBSCRIBER_EMAILS') || '')
      .split(',').map(e => e.trim()).filter(e => e.includes('@')),
    githubRepo: props.getProperty('GITHUB_REPO') || 'dissent00/open-local-weather',
    githubBranch: props.getProperty('GITHUB_BRANCH') || 'main',
    locationName: props.getProperty('LOCATION_NAME') || 'Kisumu, Kenya',
    timezone: props.getProperty('TIMEZONE') || 'Africa/Nairobi',
    publicUrl: sanitizePublicUrl(props.getProperty('PUBLIC_URL')),
  };
}

const RETRY_MAX_ATTEMPTS = 3;
const RETRY_DELAY_MS = 90 * 1000; // 90s between attempts — 3 attempts is ~3 min of sleep, well under the 6-min consumer execution cap

// Script Property keys marking "already sent today" per run type — see
// alreadySentToday()/markSentToday(). Distinct from LOCATION_NAME etc.
// (user-configured); these two are written by the script itself.
const LAST_SENT_MORNING_KEY = 'LAST_SENT_MORNING';
const LAST_SENT_EVENING_KEY = 'LAST_SENT_EVENING';

/** True if `markerKey` was already marked sent for `todayStr` — i.e. an
 * earlier trigger slot today already found real data and sent it. Backed
 * by a Script Property so the check survives across separate trigger
 * executions (each trigger firing is its own isolated script run, with no
 * shared in-memory state). Comparing against a date string, not just a
 * boolean, means the marker naturally "resets" itself the next calendar
 * day without any separate cleanup step — same pattern as the Python
 * pipeline's own last_verified_target_date guard (verify/pipeline.py). */
function alreadySentToday(markerKey, todayStr) {
  return PropertiesService.getScriptProperties().getProperty(markerKey) === todayStr;
}

function markSentToday(markerKey, todayStr) {
  PropertiesService.getScriptProperties().setProperty(markerKey, todayStr);
}

function sendDailyForecastEmail() {
  const config = getConfig();
  if (!config.subscriberEmails.length) {
    Logger.log('No subscriber emails configured in Script Properties (SUBSCRIBER_EMAILS) — nothing to send.');
    return;
  }

  const todayStr = Utilities.formatDate(new Date(), config.timezone, 'yyyy-MM-dd');
  if (alreadySentToday(LAST_SENT_MORNING_KEY, todayStr)) {
    Logger.log(`Morning forecast for ${todayStr} was already sent by an earlier trigger slot today — skipping (this is expected on a normal day with multiple trigger slots, not an error).`);
    return;
  }

  const entry = fetchForecastEntryWithRetry(config, todayStr);
  if (!entry) {
    Logger.log(`No forecast entry found for ${todayStr} after ${RETRY_MAX_ATTEMPTS} attempts — skipping this slot (the pipeline may not have run/committed yet, or is running later than usual today; a later trigger slot today will retry).`);
    return;
  }

  const subject = `[${config.locationName} Weather] Daily Forecast — ${todayStr}`;
  sendEntryEmail(config, entry, todayStr, subject, null);
  markSentToday(LAST_SENT_MORNING_KEY, todayStr);
  Logger.log(`Sent ${todayStr} morning forecast.`);
}

/** Second daily send, paired with the pipeline's evening refresh run
 * (~18:07 EAT — see README/ARCHITECTURE for why that run exists). Fired
 * from its own Apps Script trigger — see createEveningRefreshTrigger()
 * below — independent of the morning trigger. Waits not just for the
 * day's file to exist (it already does, from the morning run) but for
 * `meta.refreshed_at` to actually be set, i.e. the evening pipeline has
 * genuinely merged fresh narrative/data into it — otherwise this would
 * just resend the morning content again under a different subject.
 */
function sendEveningRefreshEmail() {
  const config = getConfig();
  if (!config.subscriberEmails.length) {
    Logger.log('No subscriber emails configured in Script Properties (SUBSCRIBER_EMAILS) — nothing to send (evening update).');
    return;
  }

  const todayStr = Utilities.formatDate(new Date(), config.timezone, 'yyyy-MM-dd');
  if (alreadySentToday(LAST_SENT_EVENING_KEY, todayStr)) {
    Logger.log(`Evening update for ${todayStr} was already sent by an earlier trigger slot today — skipping (this is expected on a normal day with multiple trigger slots, not an error).`);
    return;
  }

  const entry = fetchForecastEntryWithRetry(config, todayStr, isRefreshedEntry);
  if (!entry) {
    Logger.log(`No refreshed forecast entry found for ${todayStr} after ${RETRY_MAX_ATTEMPTS} attempts — skipping this slot (the evening refresh pipeline may not have run/committed yet, or the morning entry itself is missing; a later trigger slot today will retry).`);
    return;
  }

  const subject = `[${config.locationName} Weather] Evening Update — ${todayStr}`;
  sendEntryEmail(config, entry, todayStr, subject, 'Evening Update');
  markSentToday(LAST_SENT_EVENING_KEY, todayStr);
  Logger.log(`Sent ${todayStr} evening update.`);
}

function isRefreshedEntry(entry) {
  return !!(entry && entry.meta && entry.meta.refreshed_at);
}

/** Shared per-recipient send loop for both sendDailyForecastEmail() and
 * sendEveningRefreshEmail() — identical mechanics, only the subject and
 * runLabel differ. */
function sendEntryEmail(config, entry, dateStr, subject, runLabel) {
  const body = buildEmailPlainText(config, entry, dateStr, runLabel);
  const htmlBody = buildEmailHtml(config, entry, dateStr, runLabel);

  let sentCount = 0;
  config.subscriberEmails.forEach(email => {
    try {
      // Both `body` (true plain text) and `htmlBody` (the same text,
      // monospaced) are sent together: MailApp uses htmlBody for
      // HTML-capable clients and falls back to `body` for anything that
      // can't render HTML — see buildEmailHtml()'s doc comment for why
      // that split matters here.
      MailApp.sendEmail({ to: email, subject, body, htmlBody });
      sentCount++;
    } catch (e) {
      // Best-effort per-recipient — one bad address shouldn't block the
      // rest, same as the original pipeline's sendEmailBroadcast().
      Logger.log(`Failed to send to ${email}: ${e}`);
    }
  });

  Logger.log(`Sent ${dateStr} (${runLabel || 'morning'}) to ${sentCount}/${config.subscriberEmails.length} subscriber(s).`);
}

/** Retries fetchForecastEntry a few times with a short delay between
 * attempts — see the TIMING note in the module header for why a tight gap
 * between two independently-jittery schedulers needs this. `isReady`
 * (default: entry just needs to exist) lets a caller also require some
 * condition on the entry's content, e.g. sendEveningRefreshEmail()
 * requiring `meta.refreshed_at` to be set, not merely the file to exist. */
function fetchForecastEntryWithRetry(config, dateStr, isReady) {
  const ready = isReady || (() => true);
  for (let attempt = 1; attempt <= RETRY_MAX_ATTEMPTS; attempt++) {
    const entry = fetchForecastEntry(config, dateStr);
    if (entry && ready(entry)) return entry;
    if (attempt < RETRY_MAX_ATTEMPTS) {
      Logger.log(`Forecast entry for ${dateStr} not found yet (attempt ${attempt}/${RETRY_MAX_ATTEMPTS}) — waiting ${RETRY_DELAY_MS / 1000}s and retrying.`);
      Utilities.sleep(RETRY_DELAY_MS);
    }
  }
  return null;
}

/** Fetches data/log/{dateStr}.json from GitHub's raw-content CDN. Returns
 * null (not a thrown error) on any failure — a 404 (pipeline hasn't
 * committed yet) is an expected, common condition, not a bug. */
function fetchForecastEntry(config, dateStr) {
  const url = `https://raw.githubusercontent.com/${config.githubRepo}/${config.githubBranch}/data/log/${dateStr}.json`;
  const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) return null;
  try {
    return JSON.parse(res.getContentText());
  } catch (e) {
    Logger.log(`Failed to parse forecast JSON: ${e}`);
    return null;
  }
}

// Column width the AFD-style body wraps to. 78 rather than a round 80 —
// matches NOAA's own AFD product convention and leaves a little slack for
// quoted-reply ">" prefixes without the reflow looking cramped.
const AFD_WRAP_WIDTH = 78;
const AFD_DIVIDER = '&&';

/** Builds the true plain-text email body: a nod to NOAA's Area Forecast
 * Discussion format (dot-leader ".SECTION NAME..." headers, "&&" segment
 * dividers, fixed-width reflowed prose) rather than a styled HTML layout.
 * This is what non-HTML mail clients see, and it's also the text reused
 * (verbatim, inside a monospace <pre>) by buildEmailHtml() below — so the
 * two representations can never drift apart or say different things.
 *
 * Deliberately NOT a literal AFD clone: no fake WMO transmission header
 * ("000", station ID, product code) — this is a hobby project's forecast,
 * not an NWS product, and a convincing-looking official header would cut
 * against the disclaimer this function exists partly to carry.
 */
function buildEmailPlainText(config, entry, dateStr, runLabel) {
  const issuedStr = Utilities.formatDate(new Date(), config.timezone, 'yyyy-MM-dd HH:mm');
  const discussion = convertMarkdownToAfdText(entry.narrative_markdown || '');
  const locationLine = runLabel ? `${config.locationName} — ${runLabel}` : config.locationName;

  const lines = [];
  lines.push('Open Local Weather — Experimental Forecast Discussion');
  lines.push(locationLine);
  lines.push(`Issued ${issuedStr} (${config.timezone})`);
  lines.push('');
  lines.push(AFD_DIVIDER);
  lines.push('');
  lines.push('.DISCLAIMER...');
  lines.push(wrapText(
    'This is an experimental, AI-assisted hobby forecast product, not an ' +
    'official government forecast. It is provided for general interest ' +
    'only and must NOT be relied on for life-safety decisions. For ' +
    'official warnings and advisories, consult your national ' +
    'meteorological service (in Kenya: the Kenya Meteorological ' +
    'Department, meteo.go.ke).',
    AFD_WRAP_WIDTH
  ));
  if (runLabel) {
    lines.push('');
    lines.push(wrapText(
      'This is an evening refresh of the forecast issued earlier today, ' +
      're-synthesized on the freshest available model data for the rest ' +
      "of today and tomorrow. It does not change today's accuracy-" +
      'tracking record — only the morning run counts toward model ' +
      'verification.',
      AFD_WRAP_WIDTH
    ));
  }
  lines.push('');
  lines.push(AFD_DIVIDER);
  lines.push('');
  lines.push(discussion);
  lines.push('');
  lines.push(AFD_DIVIDER);
  lines.push('');
  lines.push('.ABOUT THIS PRODUCT...');
  lines.push(wrapText(
    "Open Local Weather synthesizes multiple numerical weather models " +
    "and tracks each one's real-world accuracy over time, broken out by " +
    "forecast lead time. Full forecast, model verification history, and " +
    "methodology are published here:",
    AFD_WRAP_WIDTH
  ));
  lines.push('');
  if (config.publicUrl) {
    lines.push(`  ${config.publicUrl}`);
    lines.push('');
    lines.push('Past forecasts and the full accuracy record:');
    lines.push(`  ${config.publicUrl}archive/`);
    lines.push('');
  }
  lines.push('You are receiving this because you subscribed to this experimental');
  lines.push('forecast service.');
  lines.push('');
  lines.push(AFD_DIVIDER);
  lines.push('$$');

  return lines.join('\n');
}

/** HTML companion to buildEmailPlainText(): the exact same text, wrapped
 * in a monospace <pre> block so HTML-capable clients render it in
 * Courier/fixed-width too (a plain-text `body` alone typically renders in
 * a client's default sans-serif font, not monospace — this is what
 * actually delivers the "plaintext/courier" look for most subscribers,
 * since most mail clients prefer htmlBody when both are present). Never
 * builds the HTML from markdown independently — see buildEmailPlainText's
 * doc comment for why that would risk the two versions saying different
 * things.
 */
function buildEmailHtml(config, entry, dateStr, runLabel) {
  const plainText = buildEmailPlainText(config, entry, dateStr, runLabel);
  const escaped = escapeHtml(plainText);
  return `<pre style="font-family: 'Courier New', Courier, monospace; font-size: 13px; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; color: #111; background: #fff; margin: 0;">${escaped}</pre>`;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/** Converts the LLM's narrative_markdown into AFD-style plain text:
 * "## Heading" -> ".HEADING..." (dot-leader, matches AFD's own
 * ".NEAR TERM..."-style segment headers), "### Subheading" -> an indented
 * "...Subheading..." one level down, "**bold**" markers stripped (plain
 * text can't bold), and prose reflowed to AFD_WRAP_WIDTH columns. Blank
 * lines separate paragraphs; "- " / "* " bullets are preserved as an
 * indented list rather than being folded into paragraph prose.
 */
function convertMarkdownToAfdText(md) {
  const lines = (md || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let paragraphBuf = [];

  function flushParagraph() {
    if (paragraphBuf.length) {
      const text = paragraphBuf.join(' ').replace(/\s+/g, ' ').trim();
      if (text) out.push(wrapText(text, AFD_WRAP_WIDTH));
      paragraphBuf = [];
    }
  }

  lines.forEach(rawLine => {
    const line = rawLine.replace(/\*\*(.*?)\*\*/g, '$1').trim();
    const h2 = line.match(/^##\s+(.*)$/);
    const h3 = line.match(/^###\s+(.*)$/);
    if (h2) {
      flushParagraph();
      out.push('');
      out.push(`.${h2[1].toUpperCase()}...`);
    } else if (h3) {
      flushParagraph();
      out.push('');
      out.push(`...${h3[1]}...`);
    } else if (line === '') {
      flushParagraph();
    } else if (/^[-*]\s+/.test(line)) {
      flushParagraph();
      out.push(wrapText('- ' + line.replace(/^[-*]\s+/, ''), AFD_WRAP_WIDTH));
    } else {
      paragraphBuf.push(line);
    }
  });
  flushParagraph();

  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

/** Greedy word-wrap to `width` columns — no external formatting library
 * is available inside Apps Script, and the reflow only needs to be good
 * enough for a monospace email body, not typographically perfect. */
function wrapText(text, width) {
  const words = text.split(/\s+/).filter(Boolean);
  const lines = [];
  let line = '';
  words.forEach(word => {
    if (line.length === 0) {
      line = word;
    } else if ((line + ' ' + word).length <= width) {
      line += ' ' + word;
    } else {
      lines.push(line);
      line = word;
    }
  });
  if (line) lines.push(line);
  return lines.join('\n');
}

// Trigger slots, spread across ~2 hours past the pipeline's own target
// time, so the mailer has a real chance of catching a pipeline run that
// lands very late — real observed GitHub Actions scheduling delays have
// ranged from a total no-show to a ~2h13m cluster delay (see the TIMING
// note in the module header for the evidence). The pipeline's morning
// target is ~06:07 EAT; evening is ~18:07 EAT. Each slot's own attempt
// still gets fetchForecastEntryWithRetry()'s few short retries on top of
// this spread — this is coarse, multi-hour coverage, not a replacement
// for that fine-grained one. alreadySentToday() is what makes adding
// slots safe: only the first slot each day that finds real data sends;
// every other slot that day is a fast no-op, not a duplicate email.
const MORNING_TRIGGER_SLOTS = [
  { hour: 6, minute: 20 },
  { hour: 6, minute: 50 },
  { hour: 7, minute: 20 },
  { hour: 7, minute: 50 },
  { hour: 8, minute: 20 },
];
const EVENING_TRIGGER_SLOTS = [
  { hour: 18, minute: 20 },
  { hour: 18, minute: 50 },
  { hour: 19, minute: 20 },
  { hour: 19, minute: 50 },
  { hour: 20, minute: 20 },
];

function formatSlotList(slots) {
  return slots.map(s => `${s.hour}:${String(s.minute).padStart(2, '0')}`).join(', ');
}

/** Registers (or re-registers) ALL morning trigger slots. Deliberately
 * scoped to triggers whose handler is sendDailyForecastEmail — an earlier
 * version of this function blanket-deleted *every* project trigger, which
 * would silently wipe out an evening trigger (see
 * createEveningRefreshTrigger()) registered separately. Safe to re-run any
 * time without touching the other trigger's slots — deletes and rebuilds
 * only its own handler's triggers, so re-running after changing
 * MORNING_TRIGGER_SLOTS cleanly replaces the old slot set rather than
 * accumulating duplicates. */
function createDailyTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'sendDailyForecastEmail')
    .forEach(t => ScriptApp.deleteTrigger(t));
  const config = getConfig();
  MORNING_TRIGGER_SLOTS.forEach(({ hour, minute }) => {
    ScriptApp.newTrigger('sendDailyForecastEmail')
      .timeBased().atHour(hour).nearMinute(minute).everyDays(1).inTimezone(config.timezone).create();
  });
  Logger.log(`${MORNING_TRIGGER_SLOTS.length} morning email trigger slot(s) registered (${formatSlotList(MORNING_TRIGGER_SLOTS)} ${config.timezone}).`);
}

/** Registers (or re-registers) ALL evening-update trigger slots — pairs
 * with the pipeline's ~18:07 EAT evening refresh run the same way the
 * morning slots pair with the ~06:07 EAT morning run. Scoped to its own
 * handler for the same reason as createDailyTrigger(). */
function createEveningRefreshTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'sendEveningRefreshEmail')
    .forEach(t => ScriptApp.deleteTrigger(t));
  const config = getConfig();
  EVENING_TRIGGER_SLOTS.forEach(({ hour, minute }) => {
    ScriptApp.newTrigger('sendEveningRefreshEmail')
      .timeBased().atHour(hour).nearMinute(minute).everyDays(1).inTimezone(config.timezone).create();
  });
  Logger.log(`${EVENING_TRIGGER_SLOTS.length} evening-update email trigger slot(s) registered (${formatSlotList(EVENING_TRIGGER_SLOTS)} ${config.timezone}).`);
}
