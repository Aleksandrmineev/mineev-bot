# mineev-bot

WhatsApp automation service built on [Baileys](https://github.com/WhiskeySockets/Baileys). Runs as a standalone Node.js app (no browser/Chromium needed) and exposes a small HTTP API so other projects (e.g. taxi-vorbestellungen) can trigger WhatsApp messages without embedding the WhatsApp session themselves.

## Endpoints

- `GET /health` — `{ ok: true, connected: boolean }`
- `POST /notify` — body `{ "to": "<phone or JID>", "message": "<text>" }`, header `Authorization: Bearer <AUTH_TOKEN>`

`to` accepts either a raw phone number with country code (e.g. `4915112345678`) or a full JID (`4915112345678@s.whatsapp.net`).

## Local setup

```bash
npm install
cp .env.example .env   # edit AUTH_TOKEN
node --env-file=.env index.js
```

On first run it prints a QR code in the terminal — scan it from WhatsApp on your phone: **Settings → Linked devices → Link a device**. The session is saved under `./auth/` (git-ignored) and reused on restarts.

## Deploying on Hostinger (shared hosting, Passenger)

1. In hPanel → **Advanced → Node.js**, create an application:
   - Node.js version: 20 or 22
   - Application root: e.g. `whatsapp-bot`
   - Application startup file: `index.js`
2. Set environment variables in the hPanel app screen: `AUTH_TOKEN` (and `PORT` if hPanel doesn't set it automatically — Passenger usually injects `PORT` itself).
3. Deploy the code into the application root, e.g. via SSH:
   ```bash
   cd ~/whatsapp-bot
   git clone git@github.com:Aleksandrmineev/mineev-bot.git .
   ```
   (or `git pull` on subsequent deploys)
4. In hPanel, click **Run NPM Install** (or via SSH: activate the app's venv shown on the hPanel screen, then `npm install`).
5. Restart the app from hPanel. Check its log (hPanel → Node.js app → Logs, or via SSH) for the QR code and scan it once.
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
