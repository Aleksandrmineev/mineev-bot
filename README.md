# mineev-bot

WhatsApp automation service built on [Baileys](https://github.com/WhiskeySockets/Baileys). Runs as a standalone Node.js app (no browser/Chromium needed) and exposes a small HTTP API so other projects (e.g. taxi-vorbestellungen) can trigger WhatsApp messages without embedding the WhatsApp session themselves.

## Dashboard

`GET /` is a small admin page (password = `AUTH_TOKEN`, session cookie, no token in the URL) showing:
- connection status and recent send activity
- a "Show QR code" button — pairing is only ever started **on demand** from here, never in an automatic loop, to avoid tripping WhatsApp's device-link rate limit
- the list of joined WhatsApp groups with their JIDs (needed to send to a group)
- a test-message form

## External API

- `GET /health` — `{ ok: true, connected: boolean }`, unauthenticated
- `POST /notify` — body `{ "to": "<phone or JID>", "message": "<text>" }`, header `Authorization: Bearer <AUTH_TOKEN>` — for other services (e.g. Google Apps Script) to trigger a message

`to` accepts either a raw phone number with country code (e.g. `4915112345678`), a full user JID (`4915112345678@s.whatsapp.net`), or a group JID (`1234567890-1234567890@g.us`, found via the dashboard's group list).

## Local setup

```bash
npm install
cp .env.example .env   # edit AUTH_TOKEN
node --env-file=.env index.js
```

Open `http://localhost:3000`, log in with `AUTH_TOKEN`, and click "Show QR code" to pair: **WhatsApp → Settings → Linked devices → Link a device**. The session is saved under `./auth/` (git-ignored) and reused on restarts.

## Deploying on Hostinger (shared hosting, Passenger)

1. In hPanel → **Advanced → Node.js**, create an application:
   - Node.js version: 20 or 22
   - Application root: e.g. `whatsapp-bot`
   - Application startup file: `index.js`
2. Set environment variables in the hPanel app screen:
   - `AUTH_TOKEN`
   - `AUTH_DIR` — an **absolute path outside the app's deploy directory** (e.g. `/home/<user>/mineev-bot-data/auth`). Each redeploy clones a fresh directory from git, so if the session lives inside it, it gets wiped on every deploy and you have to re-scan the QR code every time.
   - `PORT` only if hPanel doesn't inject it automatically (Passenger/LiteSpeed usually does).
3. Deploy the code into the application root, e.g. via SSH:
   ```bash
   cd ~/whatsapp-bot
   git clone git@github.com:Aleksandrmineev/mineev-bot.git .
   ```
   (or `git pull` on subsequent deploys)
4. In hPanel, click **Run NPM Install** (or via SSH: activate the app's venv shown on the hPanel screen, then `npm install`).
5. Restart the app from hPanel, then open the app's URL, log in, and click "Show QR code" once to pair.
6. The auth session (`~/whatsapp-bot/auth/`) lives only on the server — it is never committed to git and survives restarts/redeploys as long as the folder isn't deleted.

## Calling it from another project

```js
await fetch('https://bot.mineev.at/notify', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${process.env.MINEEV_BOT_TOKEN}`,
  },
  body: JSON.stringify({ to: '4915112345678', message: 'Ihre Fahrt wurde bestätigt.' }),
});
```
