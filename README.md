# mineev-bot

WhatsApp automation service built on [Baileys](https://github.com/WhiskeySockets/Baileys). Runs as a standalone Node.js app (no browser/Chromium needed) and exposes a small HTTP API so other projects (e.g. taxi-vorbestellungen) can trigger WhatsApp messages without embedding the WhatsApp session themselves.

The `telegram-parser/` directory also contains the job alert Telegram bot from `karriere-telegram-parser`. When enabled, the Node app starts it as a Python worker. It checks once on startup and then every `POLL_INTERVAL_SECONDS` (default and minimum: 900 seconds). Telegram commands and buttons remain available through long polling.

Freelancehunt safe monitoring uses the platform's public Telegram channel preview (`https://t.me/s/FreelancehuntProjects`), which Freelancehunt documents as a way to follow fresh projects. It does not log in, submit bids, or automate account actions. The dashboard can add this source and edit its keyword filters.

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

## Telegram job alerts on a Python-capable host

Hostinger's regular/shared web hosting does not support Python applications. This integration requires a VPS or another host with Python 3.12 or newer and permission to keep a child process running. Do not turn on `TELEGRAM_PARSER_ENABLED` on the current shared Hostinger app. On a compatible server, after deploying the updated repository:

```bash
python3.12 -m venv ~/mineev-bot-data/venv
~/mineev-bot-data/venv/bin/python -m pip install -e ./telegram-parser
mkdir -p ~/mineev-bot-data/telegram
```

The previous parser has `ENABLE_AMS=true`. To keep those AMS searches working, install Playwright Chromium into the same environment with `~/mineev-bot-data/venv/bin/python -m playwright install chromium` and set `ENABLE_AMS=true` on the new host. Chromium needs system libraries. The default `ENABLE_AMS=false` runs only karriere.at searches.

The previous parser database contains the saved searches and sent-job history. A consistent local backup has been placed at `telegram-parser/data/app.db` (git-ignored). Upload it to `~/mineev-bot-data/telegram/app.db` using SSH/SFTP before enabling the worker. Keep the database outside the application directory so deployments do not erase it. If the old parser keeps running after this backup, make a fresh backup before switching; stop the old parser before starting the hosted one so both do not poll the same Telegram token.

Set these environment variables for the Node application, then restart it:

```text
TELEGRAM_PARSER_ENABLED=true
TELEGRAM_PARSER_PYTHON=/home/<user>/mineev-bot-data/venv/bin/python
TELEGRAM_BOT_TOKEN=<existing bot token>
TELEGRAM_CHAT_ID=<existing chat id>
DATABASE_PATH=/home/<user>/mineev-bot-data/telegram/app.db
POLL_INTERVAL_SECONDS=900
```

The existing parser's `.env` and `telegram.env` files are intentionally not copied. Add any nondefault parser settings from them to the host environment, especially `ENABLE_AMS`, `FILTERS_CONFIG_PATH`, and request limits. `FILTERS_CONFIG_PATH` defaults to the included `telegram-parser/config/filters.example.yaml`; point it to a persistent absolute path if you customize filters. The worker writes to the Node app log. An exit message there indicates missing dependencies or host process restrictions. Only one worker can poll a database at a time; an extra Node process exits its Telegram worker without starting a second poller.

The parser dashboard runs separately on port 8000 and is protected with HTTP Basic Auth (`admin` plus `DASHBOARD_TOKEN`): `http://<server-ip>:8000`. It shows service status, saved searches, active filters, recent matching projects, and forms for adding/removing searches. The Freelancehunt form reads the public project channel and never submits bids.

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
