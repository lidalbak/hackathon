# Stillroom sign-up page

Pre-launch waitlist page for hosts: <https://getstillroom.netlify.app>

| File | What it is |
| --- | --- |
| `index.html` | The whole page in one file: copy, styles, the animated guest screen and the sign-up form |
| `apps-script.gs` | Google Apps Script that receives sign-ups and writes them to a Google Sheet |

## How sign-ups work

The form posts email and number of rentals to a Google Apps Script web app (`SIGNUP_ENDPOINT` at the top of the script in `index.html`). The script validates the email, skips duplicates and adds a row to the sheet's "Signups" tab. The sheet is private; only its owner sees the sign-ups.

To point the form at another sheet: follow the steps at the top of `apps-script.gs`, then paste the new `/exec` URL into `SIGNUP_ENDPOINT`.

## Publish a change

Netlify serves this folder as it is, with no build step. Open the project in Netlify, go to **Deploys** and drag this `signup` folder onto the drop area. `apps-script.gs` and this README are served too but are harmless; leave them out of the upload if you prefer.

## Status

- The guest screen is an illustration of the planned wall tablet, not a live feed.
- Smoke detection needs a PM2.5 sensor (e.g. Sensirion SPS30), which is not in the Atech kit.
