/**
 * Stillroom waitlist: receives sign-ups from the landing page and adds them to this sheet.
 *
 * Setup (about 5 minutes):
 *  1. Create a Google Sheet. Rename the first tab to "Signups".
 *  2. Extensions > Apps Script. Replace the code with this file. Save.
 *  3. Deploy > New deployment > type "Web app".
 *       Execute as: Me
 *       Who has access: Anyone
 *     Click Deploy and allow access when Google asks.
 *  4. Copy the web app URL (ends in /exec) into SIGNUP_ENDPOINT in index.html.
 *
 * Columns: time, email, rentals, form (hero/footer), page.
 * The same email signing up twice is stored once.
 */

const SHEET_NAME = 'Signups';
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const UNITS = ['1', '2-5', '6-20', '21+'];

function doPost(e) {
  const p = (e && e.parameter) || {};
  if (p.website) return reply_({ ok: true }); // honeypot: bots fill hidden fields

  const email = String(p.email || '').trim().toLowerCase().slice(0, 254);
  if (!EMAIL_RE.test(email)) return reply_({ ok: false, error: 'invalid email' });
  const units = UNITS.indexOf(String(p.units)) >= 0 ? String(p.units) : '';
  const source = String(p.source || '').slice(0, 20);
  const page = String(p.page || '').slice(0, 100);

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sheet = sheet_();
    const last = sheet.getLastRow();
    const known = last > 1 ? sheet.getRange(2, 2, last - 1, 1).getValues().map(r => String(r[0])) : [];
    if (known.indexOf(email) < 0) {
      // Prefix with ' so a value like "=..." is stored as text, never run as a formula.
      sheet.appendRow([new Date(), safe_(email), safe_(units), safe_(source), safe_(page)]);
    }
  } finally {
    lock.releaseLock();
  }
  return reply_({ ok: true });
}

// Opening the URL in a browser shows that the endpoint is live.
function doGet() {
  return reply_({ ok: true, service: 'Stillroom waitlist' });
}

function sheet_() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = book.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = book.insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(['Time', 'Email', 'Rentals', 'Form', 'Page']);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function safe_(value) {
  return /^[=+\-@]/.test(value) ? "'" + value : value;
}

function reply_(body) {
  return ContentService.createTextOutput(JSON.stringify(body)).setMimeType(ContentService.MimeType.JSON);
}
