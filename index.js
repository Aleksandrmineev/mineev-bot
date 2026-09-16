import express from 'express';
import makeWASocket, { useMultiFileAuthState, DisconnectReason } from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import qrcode from 'qrcode-terminal';

const PORT = process.env.PORT || 3000;
const AUTH_TOKEN = process.env.AUTH_TOKEN;

if (!AUTH_TOKEN) {
  console.warn('AUTH_TOKEN is not set — /notify will reject every request');
}

let sock;

async function startSock() {
  const { state, saveCreds } = await useMultiFileAuthState('./auth');

  sock = makeWASocket({ auth: state });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log('Scan this QR code with WhatsApp (Linked devices):');
      qrcode.generate(qr, { small: true });
    }

    if (connection === 'close') {
      const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      console.log('Connection closed', { statusCode, shouldReconnect });
      if (shouldReconnect) startSock();
    } else if (connection === 'open') {
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
