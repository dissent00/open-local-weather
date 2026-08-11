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
 * Reuses sendEmailBroadcast()/convertMarkdownToSimpleHtml() from the
 * original KisumuForecastPipeline_v2.gs reference almost verbatim — see
 * reference/KisumuForecastPipeline_v2.gs in the main repo.
 *
 * WHAT IT FETCHES: the day's committed forecast JSON directly from
 * GitHub's raw-content CDN (data/log/YYYY-MM-DD.json) — structured data,
 * not a scrape of the rendered GitHub Pages HTML. Same underlying
 * forecast either way, but immune to any future page-template change.
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
 * 4. Done. The trigger fires daily at 07:00 in TIMEZONE — one hour after
 *    the main pipeline's own 06:00 run, giving it time to commit that
 *    day's file first. If a day's file isn't there yet (pipeline failed,
 *    or hasn't run), sendDailyForecastEmail() logs it and skips sending
 *    rather than erroring or sending stale content.
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

function sendDailyForecastEmail() {
  const config = getConfig();
  if (!config.subscriberEmails.length) {
    Logger.log('No subscriber emails configured in Script Properties (SUBSCRIBER_EMAILS) — nothing to send.');
    return;
  }

  const todayStr = Utilities.formatDate(new Date(), config.timezone, 'yyyy-MM-dd');
  const entry = fetchForecastEntry(config, todayStr);
  if (!entry) {
    Logger.log(`No forecast entry found for ${todayStr} yet — skipping (the pipeline may not have run/committed yet).`);
    return;
  }

  const subject = `[${config.locationName} Weather] Daily Forecast — ${todayStr}`;
  const htmlBody = buildEmailHtml(config, entry, todayStr);

  let sentCount = 0;
  config.subscriberEmails.forEach(email => {
    try {
      MailApp.sendEmail({ to: email, subject, htmlBody });
      sentCount++;
    } catch (e) {
      // Best-effort per-recipient — one bad address shouldn't block the
      // rest, same as the original pipeline's sendEmailBroadcast().
      Logger.log(`Failed to send to ${email}: ${e}`);
    }
  });

  Logger.log(`Sent ${todayStr} forecast to ${sentCount}/${config.subscriberEmails.length} subscriber(s).`);
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

function buildEmailHtml(config, entry, dateStr) {
  const narrativeHtml = convertMarkdownToSimpleHtml(entry.narrative_markdown || '');
  const publicUrlLine = config.publicUrl
    ? `<p style="font-size: 0.85em;"><a href="${config.publicUrl}">View on the web</a> &middot; <a href="${config.publicUrl}archive/">Archive</a></p>`
    : '';
  return `
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 650px; margin: 0 auto;">
      <h2 style="color: #1a73e8; margin-bottom: 5px;">${config.locationName} Daily Forecast</h2>
      <p style="font-size: 0.9em; color: #666; margin-top: 0;">Date: ${dateStr}</p>
      <hr style="border: 0; border-top: 1px solid #ccc;"/>
      <div>${narrativeHtml}</div>
      <hr style="border: 0; border-top: 1px solid #ccc; margin-top: 20px;"/>
      ${publicUrlLine}
      <p style="font-size: 0.8em; color: #888;">You are receiving this because you subscribed to this forecast service.</p>
    </div>`;
}

/** Ported verbatim from the original KisumuForecastPipeline_v2.gs. */
function convertMarkdownToSimpleHtml(md) {
  return md
    .replace(/^## (.*$)/gim, '<h3 style="color: #202124; margin-top: 18px; border-bottom: 1px solid #eee; padding-bottom: 4px;">$1</h3>')
    .replace(/^### (.*$)/gim, '<h4 style="color: #3c4043; margin-top: 12px;">$1</h4>')
    .replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>')
    .replace(/\n/g, '<br/>');
}

function createDailyTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  const config = getConfig();
  ScriptApp.newTrigger('sendDailyForecastEmail')
    .timeBased().atHour(7).everyDays(1).inTimezone(config.timezone).create();
  Logger.log(`Daily 7 AM ${config.timezone} email trigger registered.`);
}
