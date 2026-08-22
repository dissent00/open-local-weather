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
 * TIMING: there is ONE send function and ONE trigger. It checks every
 * CHECK_EVERY_MINUTES and sends whenever it finds an issuance it has not
 * sent yet, identified by the entry's own timestamp (meta.refreshed_at
 * once re-issued, meta.generated_at_utc otherwise).
 *
 * This replaced a morning/evening pair with fixed slot lists. Two things
 * made that design obsolete. The pipeline can now be scheduled to run any
 * number of times a day and each run knows what time it is, so "the
 * evening one" stopped being something the mailer should know about. And
 * the several slots per send were never issuances — they were RETRIES,
 * added after real evidence that GitHub Actions' scheduling can be badly
 * late: this repo's run history shows daily.yml producing zero scheduled
 * runs on two separate occasions, and on another day every backup cron
 * slot firing 1h49m-2h13m late as a cluster (ROADMAP items 3 and 11,
 * ops/README.md).
 *
 * Keying on "have I sent this issuance?" covers both cases at once. A
 * pipeline that lands two hours late is picked up by whichever check
 * follows it; a pipeline that never ran is simply never sent; and a
 * fourth run a day needs no configuration here at all.
 *
 * THE MAILER DOES NOT DECIDE WHEN FORECASTS HAPPEN. The pipeline's own
 * schedule does (ops/README.md). Nothing here needs to be kept in sync
 * with it.
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
 * 3. Run createTriggers() once from the editor (Run menu). Apps Script
 *    will prompt for authorization the first time — that consent screen
 *    IS the auth mechanism; there is no separate app password step.
 * 4. Done. Emails now follow the pipeline wherever it is scheduled.
 *
 * UPGRADING from the morning/evening version: after step 3, run
 * removeLegacyTriggers() once. Apps Script does not delete a trigger when
 * its handler disappears — it keeps firing, failing, and emailing you
 * about it. Also delete the now-unused LAST_SENT_MORNING and
 * LAST_SENT_EVENING Script Properties if you like; they are ignored.
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
// the send-idempotency marker. Distinct from LOCATION_NAME etc.
// (user-configured); these two are written by the script itself.
// The issuance last emailed, as its timestamp. Replaces the old
// LAST_SENT_MORNING / LAST_SENT_EVENING pair: those counted sends per named
// slot, which cannot express "the third run today". A timestamp can.
const LAST_SENT_ISSUANCE_KEY = 'LAST_SENT_ISSUANCE';

/** True if `markerKey` was already marked sent for `todayStr` — i.e. an
 * earlier trigger slot today already found real data and sent it. Backed
 * by a Script Property so the check survives across separate trigger
 * executions (each trigger firing is its own isolated script run, with no
 * shared in-memory state). Comparing against a date string, not just a
 * boolean, means the marker naturally "resets" itself the next calendar
 * day without any separate cleanup step — same pattern as the Python
 * pipeline's own last_verified_target_date guard (verify/pipeline.py). */
/** The only send function.
 *
 * There is deliberately no morning/evening split any more. The pipeline can
 * now be scheduled to run any number of times a day, and each run knows what
 * time it is, so "the evening one" stopped being a thing the mailer should
 * know about.
 *
 * Instead this asks a simpler question every time it fires: IS THERE AN
 * ISSUANCE HERE I HAVE NOT SENT? The day's entry carries the moment it was
 * last written — meta.refreshed_at once it has been re-issued, otherwise
 * meta.generated_at_utc — and that value is the identity of the issuance.
 * Sending is "this differs from the last one I sent", which works identically
 * for two runs a day or five, and needs no configuration to match.
 *
 * That also collapses the old trigger design. The several slots per send
 * existed because GitHub Actions' scheduling can be hours late (see the
 * module header), so they were RETRIES, not issuances. Once sending keys on
 * novelty rather than on the clock, a single regular poll covers both: a late
 * pipeline is picked up by whichever check follows it, and a pipeline that
 * never ran is simply never sent.
 */
function sendForecastEmail() {
  const config = getConfig();
  if (!config.subscriberEmails.length) {
    Logger.log('No subscriber emails configured in Script Properties (SUBSCRIBER_EMAILS) — nothing to send.');
    return;
  }

  const todayStr = Utilities.formatDate(new Date(), config.timezone, 'yyyy-MM-dd');
  const entry = fetchForecastEntryWithRetry(config, todayStr);
  if (!entry) {
    Logger.log(`No forecast entry for ${todayStr} yet — skipping this check (the pipeline may not have run or committed; a later check will pick it up). This is expected, not an error.`);
    return;
  }

  const issuance = issuanceIdOf(entry);
  if (!issuance) {
    Logger.log(`Entry for ${todayStr} carries no issuance timestamp — cannot tell whether it is new, so not sending. This should not happen; check the pipeline's meta block.`);
    return;
  }

  const props = PropertiesService.getScriptProperties();
  if (props.getProperty(LAST_SENT_ISSUANCE_KEY) === issuance) {
    Logger.log(`Issuance ${issuance} already sent — nothing new. (Expected on most checks.)`);
    return;
  }

  // A re-issue is announced as an update so a reader knows it supersedes what
  // they already have, rather than looking like a duplicate of it.
  const isUpdate = isRefreshedEntry(entry);
  const clock = issuanceClock(entry, config.timezone);
  const label = isUpdate ? 'Update' : 'Forecast';
  const subject = `[${config.locationName} Weather] ${label} — ${todayStr}${clock ? ' ' + clock : ''}`;

  sendEntryEmail(config, entry, todayStr, subject, isUpdate ? `Update — ${clock}` : null);
  props.setProperty(LAST_SENT_ISSUANCE_KEY, issuance);
  Logger.log(`Sent ${todayStr} ${label.toLowerCase()} (issuance ${issuance}).`);
}

/** The identity of an issuance: the moment the entry was last written.
 *
 * refreshed_at once re-issued, generated_at_utc otherwise. Comparing this
 * rather than counting sends is what makes the mailer indifferent to how many
 * times a day the pipeline runs — and what stops a second copy of the same
 * issuance going out if a check runs twice. */
function issuanceIdOf(entry) {
  if (!entry || !entry.meta) return null;
  return entry.meta.refreshed_at || entry.meta.generated_at_utc || null;
}

/** The issuance time as a local HH:MM, for the subject line. Returns '' if it
 * cannot be parsed — a missing time is not a reason to withhold an email. */
function issuanceClock(entry, timezone) {
  const raw = issuanceIdOf(entry);
  if (!raw) return '';
  const parsed = new Date(raw);
  if (isNaN(parsed.getTime())) return '';
  return Utilities.formatDate(parsed, timezone, 'HH:mm');
}

function isRefreshedEntry(entry) {
  return !!(entry && entry.meta && entry.meta.refreshed_at);
}

/** Per-recipient send loop. */
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
 * condition on the entry's content, e.g. a caller that wants only a
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

  // Disclaimer deliberately lives at the BOTTOM, not right under the
  // header — moved there on request; the evening-refresh explanation
  // (when present) stays up top since it's context for reading what
  // follows, not boilerplate, and belongs next to the thing it explains.
  const lines = [];
  lines.push('Open Local Weather — Experimental Forecast Discussion');
  lines.push(locationLine);
  lines.push(`Issued ${issuedStr} (${config.timezone})`);
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
  lines.push('');
  lines.push(AFD_DIVIDER);
  lines.push('$$');

  return lines.join('\n');
}

// Site-styled HTML palette, hand-matched to docs/assets/style.css's
// LIGHT-mode (:root) values so the email visually matches the GitHub
// Pages site. Deliberately NOT attempting the site's dark-mode media
// query here — HTML email client support for `prefers-color-scheme` is
// inconsistent enough that one reliable look (the site's light mode) beats
// a half-working dark variant. Kept in sync by hand; if style.css's
// palette changes, update these too.
const SITE_BG = '#ffffff';
const SITE_FG = '#1a1a1a';
const SITE_MUTED = '#5b6470';
const SITE_BORDER = '#e2e5e9';
const SITE_ACCENT = '#1a6fd1';
const SITE_CARD_BG = '#f7f9fb';
const SITE_WARN_BG = '#fff8e6';
const SITE_WARN_BORDER = '#f0c975';
const SITE_WARN_FG = '#6b4f00';

/** Site-styled HTML companion to buildEmailPlainText(): NOT derived from
 * the plain-text body anymore (an earlier version wrapped the same AFD
 * text in a monospace <pre>, on the theory the two representations could
 * never drift apart if one was literally the other escaped). Replaced on
 * request — subscribers wanted the HTML version to look like the actual
 * GitHub Pages site (system font, the stat-grid summary, real headings)
 * for HTML-capable clients, with the fixed-width AFD look kept only as
 * the plain-text fallback for clients that can't render HTML at all. The
 * two are now independently built from the same `entry`/`config` inputs
 * rather than one being a literal transform of the other — consistency
 * of CONTENT (same narrative, same stats, same disclaimer, same links) is
 * what's guaranteed now, not byte-identical text.
 *
 * Deliberately does NOT replicate the site's per-station Ground AQI
 * Stations section — that would mean re-implementing aqi.py's staleness
 * threshold logic a second time in JS, a duplication risk for something
 * the narrative already describes qualitatively. Revisit if that's
 * wanted; not built speculatively.
 *
 * Uses an HTML <table> for the stat grid (not CSS grid/flexbox) and
 * inline styles throughout, not a linked stylesheet or <style> block —
 * both are the standard, portable choices for HTML email, since many
 * clients (Outlook in particular) strip <head>/<style> or have poor
 * modern-CSS support.
 */
function buildEmailHtml(config, entry, dateStr, runLabel) {
  const locationLine = runLabel ? `${config.locationName} — ${runLabel}` : config.locationName;
  const issuedStr = Utilities.formatDate(new Date(), config.timezone, 'yyyy-MM-dd HH:mm');
  const narrativeHtml = convertMarkdownToHtml(entry.narrative_markdown || '');
  const statGridHtml = buildStatGridHtml(entry);

  const refreshNoteHtml = runLabel
    ? `<p style="font-size: 0.9em; color: ${SITE_MUTED}; margin: 0.4em 0 1em;">This is an evening refresh of the forecast issued earlier today, re-synthesized on the freshest available model data for the rest of today and tomorrow. It does not change today's accuracy-tracking record &mdash; only the morning run counts toward model verification.</p>`
    : '';

  const linksHtml = config.publicUrl
    ? `<p style="font-size: 0.9em; margin: 1.2em 0 0.3em;"><a href="${escapeHtml(config.publicUrl)}" style="color: ${SITE_ACCENT};">View on the web</a> &middot; <a href="${escapeHtml(config.publicUrl)}archive/" style="color: ${SITE_ACCENT};">Archive</a></p>`
    : '';

  return `
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: ${SITE_FG}; background: ${SITE_BG}; max-width: 640px; margin: 0 auto; line-height: 1.5;">
  <h1 style="font-size: 1.4em; margin: 0 0 0.1em;">${escapeHtml(locationLine)}</h1>
  <p style="font-size: 0.85em; color: ${SITE_MUTED}; margin: 0 0 1em;">Forecast for ${escapeHtml(dateStr)} &middot; issued ${escapeHtml(issuedStr)} (${escapeHtml(config.timezone)}) &middot; multi-model synthesis via Open Local Weather</p>
  ${refreshNoteHtml}
  ${statGridHtml}
  <div>${narrativeHtml}</div>
  ${linksHtml}
  <p style="font-size: 0.8em; color: ${SITE_MUTED}; margin: 1.5em 0 0;">You are receiving this because you subscribed to this experimental forecast service.</p>
  <div style="background: ${SITE_WARN_BG}; border: 1px solid ${SITE_WARN_BORDER}; color: ${SITE_WARN_FG}; border-radius: 8px; padding: 0.75em 1em; margin: 1.2em 0 0; font-size: 0.85em; line-height: 1.5;">Experimental, AI-assisted forecast &mdash; not an official government product. Do not rely on this for life-safety decisions. For official warnings and advisories, consult your national meteorological service (in Kenya: the Kenya Meteorological Department, meteo.go.ke).</div>
</div>`;
}

/** Builds the site's stat-grid ("High/Low", "Rain", "Onset Window", "UV
 * Index", "Air Quality") as an HTML <table> — matches forecast.html.jinja's
 * .stat-grid section, same fields, same conditional-on-present logic
 * (onset_window/uv_index_max/air_quality_aqi only shown when the entry
 * actually has them). A <table> with the legacy `cellspacing` attribute
 * for gaps, not CSS grid/gap, since that's the reliable choice across
 * email clients including Outlook. */
function buildStatGridHtml(entry) {
  const stats = [['High / Low', entry.temp_high_low_display], ['Rain', entry.rain_expected]];
  if (entry.onset_window) stats.push(['Onset Window', entry.onset_window]);
  if (entry.uv_index_max) stats.push(['UV Index', entry.uv_index_max]);
  if (entry.air_quality_aqi) stats.push(['Air Quality', entry.air_quality_aqi]);

  const cells = stats.map(([label, value]) => `<td style="background: ${SITE_CARD_BG}; border: 1px solid ${SITE_BORDER}; border-radius: 8px; padding: 0.7em 0.9em; vertical-align: top;"><div style="font-size: 0.72em; color: ${SITE_MUTED}; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 0.15em;">${escapeHtml(label)}</div><div style="font-size: 1em; font-weight: 600; color: ${SITE_FG};">${escapeHtml(String(value))}</div></td>`).join('');

  return `<table role="presentation" cellpadding="0" cellspacing="8" style="border-collapse: separate; margin: 0.8em 0;"><tr>${cells}</tr></table>`;
}

/** Converts narrative_markdown into real HTML matching the GitHub Pages
 * site's own rendering (forecast.html.jinja / style.css): "## Heading" ->
 * <h2>, "### Subheading" -> <h3>, "**bold**" -> <strong>, blank-line-
 * separated paragraphs -> <p>, "- "/"* " bullets -> <ul><li>. See
 * convertMarkdownToAfdText() for the plain-text sibling of this — same
 * source markdown, different rendering target. Inline-styled, not a
 * linked stylesheet, for the same email-client-compatibility reason as
 * buildEmailHtml() above.
 */
function convertMarkdownToHtml(md) {
  const lines = (md || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let paragraphBuf = [];
  let listBuf = [];

  function inlineFormat(text) {
    return escapeHtml(text).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  }

  function flushParagraph() {
    if (paragraphBuf.length) {
      const text = paragraphBuf.join(' ').replace(/\s+/g, ' ').trim();
      if (text) out.push(`<p style="margin: 0.6em 0; line-height: 1.6;">${inlineFormat(text)}</p>`);
      paragraphBuf = [];
    }
  }

  function flushList() {
    if (listBuf.length) {
      const items = listBuf.map(item => `<li style="margin: 0.2em 0;">${inlineFormat(item)}</li>`).join('');
      out.push(`<ul style="margin: 0.6em 0; padding-left: 1.4em;">${items}</ul>`);
      listBuf = [];
    }
  }

  lines.forEach(rawLine => {
    const line = rawLine.trim();
    const h2 = line.match(/^##\s+(.*)$/);
    const h3 = line.match(/^###\s+(.*)$/);
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (h2) {
      flushParagraph();
      flushList();
      out.push(`<h2 style="font-size: 1.15em; margin: 1.4em 0 0.5em; padding-bottom: 0.25em; border-bottom: 1px solid ${SITE_BORDER}; color: ${SITE_FG};">${inlineFormat(h2[1])}</h2>`);
    } else if (h3) {
      flushParagraph();
      flushList();
      out.push(`<h3 style="font-size: 1em; margin: 1.1em 0 0.3em; color: ${SITE_MUTED};">${inlineFormat(h3[1])}</h3>`);
    } else if (bullet) {
      flushParagraph();
      listBuf.push(bullet[1]);
    } else if (line === '') {
      flushParagraph();
      flushList();
    } else {
      flushList();
      paragraphBuf.push(line);
    }
  });
  flushParagraph();
  flushList();

  return out.join('\n');
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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

// ---------------------------------------------------------------------
// WHEN TO CHECK  —  the one setting to change
// ---------------------------------------------------------------------
//
// This is NOT the forecast schedule. The pipeline decides when forecasts are
// produced (its own cron — see ops/README.md); the mailer only notices that
// one has appeared and sends it. So there is nothing here to keep in sync
// with the pipeline: schedule the pipeline for two runs a day or five, and
// the emails follow on their own.
//
// The poll exists because GitHub Actions' scheduling can be badly late. This
// project's own run history shows daily.yml producing ZERO scheduled runs on
// two separate occasions, and on another day every backup cron slot firing
// 1h49m-2h13m late as a cluster (ROADMAP items 3 and 11). Fixed send times
// cannot survive that; checking regularly can.
//
// Every 30 minutes is 48 checks a day, nearly all of them a fetch and an
// early return. Well inside Apps Script's consumer quota. Raise it if you
// want emails to land closer to the moment a forecast is published; lower it
// if you are near a quota ceiling.
const CHECK_EVERY_MINUTES = 30;

/** Registers (or re-registers) the single polling trigger.
 *
 * Safe to re-run: it deletes only its own handler's triggers first, so
 * changing CHECK_EVERY_MINUTES and running this again replaces the schedule
 * rather than accumulating duplicates.
 *
 * If you are upgrading from the old two-trigger setup, run
 * removeLegacyTriggers() once afterwards — the old handlers no longer exist
 * in this file, and a trigger pointing at a missing function fails on every
 * firing and mails you about it.
 */
function createTriggers() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'sendForecastEmail')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('sendForecastEmail')
    .timeBased().everyMinutes(CHECK_EVERY_MINUTES).create();
  Logger.log(`Forecast mailer will check every ${CHECK_EVERY_MINUTES} minutes and send whenever a new issuance appears.`);
}

/** Deletes triggers left over from the old morning/evening design.
 *
 * Those handlers are gone from this file, and Apps Script does not remove a
 * trigger when its function disappears — it keeps firing, failing, and
 * emailing the owner about it. Run once after upgrading; harmless thereafter.
 */
function removeLegacyTriggers() {
  const legacy = ['sendDailyForecastEmail', 'sendEveningRefreshEmail'];
  const found = ScriptApp.getProjectTriggers().filter(t => legacy.indexOf(t.getHandlerFunction()) !== -1);
  found.forEach(t => ScriptApp.deleteTrigger(t));
  Logger.log(found.length
    ? `Removed ${found.length} trigger(s) from the old morning/evening setup.`
    : 'No legacy triggers found — nothing to remove.');
}
