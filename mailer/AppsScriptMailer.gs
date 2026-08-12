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
 * TIMING: the trigger fires at ~06:20 in TIMEZONE, only 20 minutes after
 * the main pipeline's own 06:00 cron. Both GitHub Actions' scheduled
 * triggers and Apps Script's own time-based triggers are documented as
 * able to fire several minutes later than requested under load — two
 * independent sources of jitter with only a 20-minute gap between them.
 * To de-risk that, fetchForecastEntryWithRetry() retries a few times with
 * a short delay before giving up for the day, comfortably within Apps
 * Script's 6-minute execution limit for consumer accounts. A day where
 * BOTH systems are unusually late can still be missed silently (by
 * design — see fetchForecastEntry()'s doc comment) rather than erroring.
 *
 * ============================== SETUP ==============================
 * 1. script.google.com -> New project -> replace Code.gs's contents with
 *    this file.
 * 2. Project Settings (gear icon) -> Script Properties -> add:
 *      SUBSCRIBER_EMAILS  comma-separated recipient list, e.g.
 *                         "a@example.com,b@example.com"
 *      GITHUB_REPO        "dissent00/open-local-weather" (or your fork)
 *      GITHUB_BRANCH      "main"
 *      LOCATION_NAME      "Kisumu, Kenya" (match location.yaml's
 *                         primary_place_name)
 *      TIMEZONE           "Africa/Nairobi" (match location.yaml's timezone)
 *      PUBLIC_URL         "https://<owner>.github.io/<repo>/" (linked in
 *                         the email footer; optional, leave blank to omit)
 * 3. Run createDailyTrigger() once from the editor (Run menu). Apps
 *    Script will prompt for authorization the first time — that consent
 *    screen IS the auth mechanism; there's no separate app password step.
 * 4. Done. The trigger fires daily at ~06:20 in TIMEZONE, retrying a few
 *    times (see TIMING above) if that day's file isn't committed yet. If
 *    it's still not there after retrying, sendDailyForecastEmail() logs it
 *    and skips sending for the day rather than erroring or sending stale
 *    content.
 * =====================================================================
 */

function getConfig() {
  const props = PropertiesService.getScriptProperties();
  return {
    subscriberEmails: (props.getProperty('SUBSCRIBER_EMAILS') || '')
      .split(',').map(e => e.trim()).filter(e => e.includes('@')),
    githubRepo: props.getProperty('GITHUB_REPO') || 'dissent00/open-local-weather',
    githubBranch: props.getProperty('GITHUB_BRANCH') || 'main',
    locationName: props.getProperty('LOCATION_NAME') || 'Kisumu, Kenya',
    timezone: props.getProperty('TIMEZONE') || 'Africa/Nairobi',
    publicUrl: props.getProperty('PUBLIC_URL') || '',
  };
}

const RETRY_MAX_ATTEMPTS = 3;
const RETRY_DELAY_MS = 90 * 1000; // 90s between attempts — 3 attempts is ~3 min of sleep, well under the 6-min consumer execution cap

function sendDailyForecastEmail() {
  const config = getConfig();
  if (!config.subscriberEmails.length) {
    Logger.log('No subscriber emails configured in Script Properties (SUBSCRIBER_EMAILS) — nothing to send.');
    return;
  }

  const todayStr = Utilities.formatDate(new Date(), config.timezone, 'yyyy-MM-dd');
  const entry = fetchForecastEntryWithRetry(config, todayStr);
  if (!entry) {
    Logger.log(`No forecast entry found for ${todayStr} after ${RETRY_MAX_ATTEMPTS} attempts — skipping (the pipeline may not have run/committed yet, or is running later than usual today).`);
    return;
  }

  const subject = `[${config.locationName} Weather] Daily Forecast — ${todayStr}`;
  const body = buildEmailPlainText(config, entry, todayStr);
  const htmlBody = buildEmailHtml(config, entry, todayStr);

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

  Logger.log(`Sent ${todayStr} forecast to ${sentCount}/${config.subscriberEmails.length} subscriber(s).`);
}

/** Retries fetchForecastEntry a few times with a short delay between
 * attempts — see the TIMING note in the module header for why a tight gap
 * between two independently-jittery schedulers needs this. */
function fetchForecastEntryWithRetry(config, dateStr) {
  for (let attempt = 1; attempt <= RETRY_MAX_ATTEMPTS; attempt++) {
    const entry = fetchForecastEntry(config, dateStr);
    if (entry) return entry;
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
function buildEmailPlainText(config, entry, dateStr) {
  const issuedStr = Utilities.formatDate(new Date(), config.timezone, 'yyyy-MM-dd HH:mm');
  const discussion = convertMarkdownToAfdText(entry.narrative_markdown || '');

  const lines = [];
  lines.push('Open Local Weather — Experimental Forecast Discussion');
  lines.push(config.locationName);
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
function buildEmailHtml(config, entry, dateStr) {
  const plainText = buildEmailPlainText(config, entry, dateStr);
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

function createDailyTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  const config = getConfig();
  ScriptApp.newTrigger('sendDailyForecastEmail')
    .timeBased().atHour(6).nearMinute(20).everyDays(1).inTimezone(config.timezone).create();
  Logger.log(`Daily ~6:20 AM ${config.timezone} email trigger registered.`);
}
