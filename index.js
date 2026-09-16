import express from 'express';
import makeWASocket, { useMultiFileAuthState, DisconnectReason } from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import qrcodeTerminal from 'qrcode-terminal';
import QRCode from 'qrcode';

const PORT = process.env.PORT || 3000;
const AUTH_TOKEN = process.env.AUTH_TOKEN;
const AUTH_DIR = process.env.AUTH_DIR || './auth';

if (!AUTH_TOKEN) {
  console.warn('AUTH_TOKEN is not set — /notify and /qr will reject every request');
}

let sock;
let latestQR = null;

async function startSock() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  sock = makeWASocket({ auth: state });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      latestQR = qr;
      console.log('Scan this QR code with WhatsApp (Linked devices):');
      qrcodeTerminal.generate(qr, { small: true });
    }

    if (connection === 'close') {
      const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      console.log('Connection closed', { statusCode, shouldReconnect });
      if (shouldReconnect) startSock();
    } else if (connection === 'open') {
      latestQR = null;
      console.log('WhatsApp connection established');
    }
  });
}

startSock();

const app = express();
app.use(express.json());

app.get('/health', (req, res) => {
  res.json({ ok: true, connected: Boolean(sock?.user) });
});

app.get('/qr', async (req, res) => {
  if (!AUTH_TOKEN || req.query.token !== AUTH_TOKEN) {
    return res.status(401).send('unauthorized');
  }
  if (sock?.user) {
    return res.send('Already linked to WhatsApp — no QR needed.');
  }
  if (!latestQR) {
    return res.send('No QR available yet, refresh in a few seconds.');
  }
  const dataUrl = await QRCode.toDataURL(latestQR);
  res.send(`<!doctype html><html><body style="display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
    <div style="text-align:center;font-family:sans-serif;">
      <img src="${dataUrl}" alt="WhatsApp QR" />
      <p>Scan with WhatsApp → Linked devices. Refresh if it looks stale.</p>
    </div>
  </body></html>`);
});

app.get('/groups', async (req, res) => {
  if (!AUTH_TOKEN || req.query.token !== AUTH_TOKEN) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  if (!sock?.user) {
    return res.status(503).json({ error: 'whatsapp session not connected yet' });
  }
  try {
    const groups = await sock.groupFetchAllParticipating();
    const list = Object.values(groups).map((g) => ({ id: g.id, subject: g.subject }));
    res.json({ ok: true, groups: list });
  } catch (err) {
    console.error('Failed to list groups', err);
    res.status(500).json({ error: 'failed to list groups' });
  }
});

app.post('/notify', async (req, res) => {
  if (!AUTH_TOKEN || req.headers.authorization !== `Bearer ${AUTH_TOKEN}`) {
    return res.status(401).json({ error: 'unauthorized' });
  }

  const { to, message } = req.body || {};
  if (!to || !message) {
    return res.status(400).json({ error: 'fields "to" and "message" are required' });
  }

  if (!sock?.user) {
    return res.status(503).json({ error: 'whatsapp session not connected yet' });
  }

  try {
    const jid = to.includes('@') ? to : `${to.replace(/\D/g, '')}@s.whatsapp.net`;
    await sock.sendMessage(jid, { text: message });
    res.json({ ok: true });
  } catch (err) {
    console.error('Failed to send message', err);
    res.status(500).json({ error: 'send failed' });
  }
});

app.listen(PORT, () => {
  console.log(`mineev-bot listening on port ${PORT}`);
});
