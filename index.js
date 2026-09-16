import express from 'express';
import crypto from 'crypto';
import fs from 'fs';
import path from 'path';
import makeWASocket, { useMultiFileAuthState, DisconnectReason } from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import QRCode from 'qrcode';

const PORT = process.env.PORT || 3000;
const AUTH_TOKEN = process.env.AUTH_TOKEN;
const AUTH_DIR = process.env.AUTH_DIR || './auth';
const SESSION_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

if (!AUTH_TOKEN) {
  console.warn('AUTH_TOKEN is not set — login, /notify and the dashboard API will reject every request');
}

let sock;
let latestQR = null;
let hasEverConnected = false;
let pairingInProgress = false;
const notifyLog = [];

function logNotify(entry) {
  notifyLog.unshift({ time: new Date().toISOString(), ...entry });
  if (notifyLog.length > 20) notifyLog.length = 20;
}

async function startSock() {
  if (pairingInProgress) return;
  pairingInProgress = true;
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  sock = makeWASocket({ auth: state });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      latestQR = qr;
      console.log('QR code generated, waiting to be scanned from the dashboard');
    }

    if (connection === 'close') {
      pairingInProgress = false;
      const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      console.log('Connection closed', { statusCode, hasEverConnected, loggedOut });
      // Only auto-reconnect a session that was already linked (e.g. a network blip).
      // Never auto-retry pairing — that hammers WhatsApp's device-link rate limit.
      if (hasEverConnected && !loggedOut) startSock();
      if (loggedOut) hasEverConnected = false;
    } else if (connection === 'open') {
      pairingInProgress = false;
      hasEverConnected = true;
      latestQR = null;
      console.log('WhatsApp connection established');
    }
  });
}

// Only auto-connect at boot if a session already exists. If we've never been
// paired, wait for a deliberate "Show QR code" click from the dashboard —
// contacting WhatsApp automatically on every restart is what triggers its
// device-link rate limit when the process restarts often (e.g. on deploys).
if (fs.existsSync(path.join(AUTH_DIR, 'creds.json'))) {
  startSock();
}

const app = express();
app.use(express.json());
app.use(express.urlencoded({ extended: false }));

// ---- minimal cookie-session auth (single admin user) ----

function sign(value) {
  return crypto.createHmac('sha256', AUTH_TOKEN || '').update(value).digest('hex');
}

function parseCookies(req) {
  const header = req.headers.cookie;
  const out = {};
  if (!header) return out;
  header.split(';').forEach((part) => {
    const idx = part.indexOf('=');
    if (idx === -1) return;
    out[part.slice(0, idx).trim()] = decodeURIComponent(part.slice(idx + 1).trim());
  });
  return out;
}

function hasValidSession(req) {
  if (!AUTH_TOKEN) return false;
  const cookie = parseCookies(req).sid;
  if (!cookie) return false;
  const [issuedAt, signature] = cookie.split('.');
  if (!issuedAt || !signature) return false;
  if (Date.now() - Number(issuedAt) > SESSION_MAX_AGE_MS) return false;
  const expected = sign(issuedAt);
  const a = Buffer.from(signature);
  const b = Buffer.from(expected);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function requireSession(req, res, next) {
  if (!hasValidSession(req)) return res.status(401).json({ error: 'unauthorized' });
  next();
}

function layout(body) {
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>mineev-bot</title>
  <style>
    body { font-family: -apple-system, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; color: #1d1e20; }
    h1 { font-size: 20px; }
    section { margin-bottom: 28px; padding: 16px; border: 1px solid #ddd; border-radius: 8px; }
    button { cursor: pointer; padding: 8px 14px; border-radius: 6px; border: 1px solid #ccc; background: #f4f5ff; }
    input[type=text], input[type=password] { padding: 8px; width: 100%; box-sizing: border-box; margin-bottom: 8px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    td, th { text-align: left; padding: 4px 6px; border-bottom: 1px solid #eee; }
    #qr img { max-width: 260px; }
    .ok { color: #1a7f37; } .err { color: #c62828; }
  </style></head><body>${body}</body></html>`;
}

app.get('/', (req, res) => {
  if (!hasValidSession(req)) {
    return res.send(layout(`
      <h1>mineev-bot</h1>
      <section>
        <form method="post" action="/login">
          <input type="password" name="password" placeholder="Password" required>
          <button type="submit">Log in</button>
        </form>
      </section>`));
  }
  res.send(layout(`
    <h1>mineev-bot <a href="/logout" style="float:right;font-size:13px;">Log out</a></h1>

    <section>
      <h3>Status</h3>
      <div id="status">loading…</div>
    </section>

    <section id="qrSection">
      <h3>WhatsApp pairing</h3>
      <button onclick="showQr()">Show QR code</button>
      <div id="qr"></div>
    </section>

    <section>
      <h3>Groups</h3>
      <button onclick="loadGroups()">Load groups</button>
      <div id="groups"></div>
    </section>

    <section>
      <h3>Send test message</h3>
      <input type="text" id="to" placeholder="Phone or group JID">
      <input type="text" id="message" placeholder="Message text">
      <button onclick="sendTest()">Send</button>
      <div id="sendResult"></div>
    </section>

    <section>
      <h3>Recent activity</h3>
      <table id="log"><thead><tr><th>Time</th><th>To</th><th>Status</th></tr></thead><tbody></tbody></table>
    </section>

    <script>
      async function refreshStatus() {
        const r = await fetch('/api/status');
        const d = await r.json();
        document.getElementById('status').innerHTML = d.connected
          ? '<span class="ok">Connected</span> as ' + (d.user || '')
          : '<span class="err">Not connected</span>';
        const tbody = document.querySelector('#log tbody');
        tbody.innerHTML = (d.log || []).map(e =>
          '<tr><td>' + new Date(e.time).toLocaleString() + '</td><td>' + e.to + '</td><td class="' + (e.ok ? 'ok' : 'err') + '">' + (e.ok ? 'sent' : e.error) + '</td></tr>'
        ).join('');
      }
      async function showQr() {
        document.getElementById('qr').textContent = 'Loading…';
        const r = await fetch('/api/qr');
        const d = await r.json();
        if (d.connected) document.getElementById('qr').textContent = 'Already linked — no QR needed.';
        else if (d.qr) document.getElementById('qr').innerHTML = '<img src="' + d.qr + '">';
        else document.getElementById('qr').textContent = d.message || 'No QR yet, try again in a few seconds.';
      }
      async function loadGroups() {
        document.getElementById('groups').textContent = 'Loading…';
        const r = await fetch('/api/groups');
        const d = await r.json();
        if (!d.ok) { document.getElementById('groups').textContent = d.error; return; }
        document.getElementById('groups').innerHTML = '<ul>' + d.groups.map(g => '<li><code>' + g.id + '</code> — ' + g.subject + '</li>').join('') + '</ul>';
      }
      async function sendTest() {
        const to = document.getElementById('to').value;
        const message = document.getElementById('message').value;
        const r = await fetch('/api/send-test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ to, message }) });
        const d = await r.json();
        document.getElementById('sendResult').innerHTML = d.ok ? '<span class="ok">Sent</span>' : '<span class="err">' + d.error + '</span>';
        refreshStatus();
      }
      refreshStatus();
      setInterval(refreshStatus, 5000);
    </script>
  `));
});

app.post('/login', (req, res) => {
  const password = req.body?.password || '';
  if (!AUTH_TOKEN || password !== AUTH_TOKEN) {
    return res.status(401).send(layout('<h1>mineev-bot</h1><section><p class="err">Wrong password.</p><a href="/">Back</a></section>'));
  }
  const issuedAt = String(Date.now());
  const cookie = `${issuedAt}.${sign(issuedAt)}`;
  res.setHeader('Set-Cookie', `sid=${encodeURIComponent(cookie)}; HttpOnly; Path=/; Max-Age=${SESSION_MAX_AGE_MS / 1000}; SameSite=Lax`);
  res.redirect('/');
});

app.get('/logout', (req, res) => {
  res.setHeader('Set-Cookie', 'sid=; HttpOnly; Path=/; Max-Age=0');
  res.redirect('/');
});

// ---- dashboard API (session-protected) ----

app.get('/api/status', requireSession, (req, res) => {
  res.json({ connected: Boolean(sock?.user), user: sock?.user?.id, log: notifyLog });
});

app.get('/api/qr', requireSession, async (req, res) => {
  if (sock?.user) return res.json({ connected: true });
  if (!latestQR) {
    startSock();
    return res.json({ qr: null, message: 'Starting pairing, click again in a few seconds…' });
  }
  const dataUrl = await QRCode.toDataURL(latestQR);
  res.json({ qr: dataUrl });
});

app.get('/api/groups', requireSession, async (req, res) => {
  if (!sock?.user) return res.status(503).json({ ok: false, error: 'whatsapp session not connected yet' });
  try {
    const groups = await sock.groupFetchAllParticipating();
    res.json({ ok: true, groups: Object.values(groups).map((g) => ({ id: g.id, subject: g.subject })) });
  } catch (err) {
    console.error('Failed to list groups', err);
    res.status(500).json({ ok: false, error: 'failed to list groups' });
  }
});

app.post('/api/send-test', requireSession, async (req, res) => {
  const { to, message } = req.body || {};
  if (!to || !message) return res.status(400).json({ ok: false, error: 'to and message are required' });
  const result = await sendWhatsAppMessage(to, message);
  res.status(result.ok ? 200 : 500).json(result);
});

// ---- external API (bearer-token protected, for other services e.g. Google Apps Script) ----

async function sendWhatsAppMessage(to, message) {
  if (!sock?.user) {
    logNotify({ to, ok: false, error: 'not connected' });
    return { ok: false, error: 'whatsapp session not connected yet' };
  }
  try {
    const jid = to.includes('@') ? to : `${to.replace(/\D/g, '')}@s.whatsapp.net`;
    await sock.sendMessage(jid, { text: message });
    logNotify({ to, ok: true });
    return { ok: true };
  } catch (err) {
    console.error('Failed to send message', err);
    logNotify({ to, ok: false, error: 'send failed' });
    return { ok: false, error: 'send failed' };
  }
}

app.get('/health', (req, res) => {
  res.json({ ok: true, connected: Boolean(sock?.user) });
});

app.post('/notify', async (req, res) => {
  if (!AUTH_TOKEN || req.headers.authorization !== `Bearer ${AUTH_TOKEN}`) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  const { to, message } = req.body || {};
  if (!to || !message) {
    return res.status(400).json({ error: 'fields "to" and "message" are required' });
  }
  const result = await sendWhatsAppMessage(to, message);
  res.status(result.ok ? 200 : (result.error === 'whatsapp session not connected yet' ? 503 : 500)).json(result);
});

app.listen(PORT, () => {
  console.log(`mineev-bot listening on port ${PORT}`);
});
