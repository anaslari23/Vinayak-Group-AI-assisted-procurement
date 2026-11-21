// Unofficial WhatsApp Web bridge for the procurement agent (Baileys).
// Logs in as a normal WhatsApp account (a SPARE number, scanned by QR), tracks every group message it can see, forwards them to the agent
// server, and sends back the agent's replies. This is against WhatsApp's terms of service and the number can be banned: demo use only.
import makeWASocket, { useMultiFileAuthState, DisconnectReason, downloadMediaMessage, fetchLatestBaileysVersion } from '@whiskeysockets/baileys'
import QRCode from 'qrcode'
import pino from 'pino'
import fs from 'node:fs'

const env = {}
for (const f of ['../.env', '.env']) { try { for (const l of fs.readFileSync(new URL(f, import.meta.url), 'utf8').split('\n')) { const m = l.match(/^([A-Z_]+)=(.*)$/); if (m) env[m[1]] = m[2] } } catch {} }
const APP = process.env.APP_URL || env.APP_URL || 'http://127.0.0.1:8000'
const KEY = process.env.WA_BRIDGE_KEY || env.WA_BRIDGE_KEY || ''
const H = { 'content-type': 'application/json', 'x-bridge-key': KEY }
const log = pino({ level: 'silent' })
const post = (path, body) => fetch(APP + path, { method: 'POST', headers: H, body: JSON.stringify(body) }).then(r => r.json()).catch(e => ({ error: String(e) }))
const names = new Map()

const meta = new Map()
async function groupInfo(sock, jid) {
  if (!jid.endsWith('@g.us')) return { name: '', admins: [] }
  const hit = meta.get(jid); if (hit && Date.now() - hit.at < 5 * 60 * 1000) return hit
  try {
    const md = await sock.groupMetadata(jid)
    const admins = md.participants.filter(x => x.admin).flatMap(x => [x.id, x.phoneNumber, x.lid].filter(Boolean))
    const v = { name: md.subject, admins, at: Date.now() }; meta.set(jid, v); return v
  } catch { return hit || { name: '', admins: [] } }
}

let current = null, ready = false
async function start() {
  const { state, saveCreds } = await useMultiFileAuthState('auth')
  const { version } = await fetchLatestBaileysVersion()
  const sock = makeWASocket({ version, auth: state, logger: log, browser: ['Vinayak Agent', 'Chrome', '1.0'], markOnlineOnConnect: false, syncFullHistory: false })
  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr) { console.log('QR ready: open http://127.0.0.1:8000/whatsapp and scan it with the spare number (WhatsApp > Linked devices)'); await post('/whatsapp/status', { state: 'qr', qr: await QRCode.toDataURL(qr, { width: 320, margin: 1 }) }) }
    if (connection === 'open') { ready = true; console.log('connected as', sock.user?.id); await post('/whatsapp/status', { state: 'open', me: sock.user?.id, lid: sock.user?.lid }) }
    if (connection === 'close') { ready = false;
      const code = lastDisconnect?.error?.output?.statusCode
      await post('/whatsapp/status', { state: 'closed', code })
      if (code === DisconnectReason.loggedOut) { console.log('logged out: delete the whatsapp/auth folder and restart to scan again'); process.exit(1) }
      setTimeout(start, 3000)
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    for (const m of messages) {
      try {
        if (!m.message || m.key.fromMe || m.key.remoteJid === 'status@broadcast') continue
        const jid = m.key.remoteJid; const isGroup = jid.endsWith('@g.us'); const gi = await groupInfo(sock, jid)
        const msg = m.message
        const text = msg.conversation || msg.extendedTextMessage?.text || msg.imageMessage?.caption || msg.videoMessage?.caption || ''
        const ctx = msg.extendedTextMessage?.contextInfo || msg.audioMessage?.contextInfo || {}
        const p = { msg_id: m.key.id, jid, is_group: isGroup, group_name: gi.name, admins: gi.admins, sender: m.key.participant || jid, sender_alt: m.key.participantAlt || '',
          sender_name: m.pushName || '', text, mentioned: ctx.mentionedJid || [], quoted_participant: ctx.participant || '', my_ids: [sock.user?.id, sock.user?.lid].filter(Boolean), ts: Number(m.messageTimestamp) || 0 }
        if (msg.audioMessage) { const buf = await downloadMediaMessage(m, 'buffer', {}, { logger: log, reuploadRequest: sock.updateMediaMessage }); p.audio_b64 = buf.toString('base64') }
        if (!p.text && !p.audio_b64) continue
        await post('/whatsapp/inbound', p)
      } catch (e) { console.log('inbound error', String(e).slice(0, 200)) }
    }
  })

  current = sock
}
start()

// outbox: the agent server queues replies; we deliver them with whichever socket is connected right now
setInterval(async () => {
  if (!current || !ready) return
  try {
    const r = await fetch(APP + '/whatsapp/outbox', { headers: H }); if (!r.ok) return
    const { items } = await r.json(); const done = []
    for (const it of items || []) {
      try {
        await current.sendMessage(it.jid, { text: it.text, mentions: it.mentions || [] })
        if (it.ogg_b64) await current.sendMessage(it.jid, { audio: Buffer.from(it.ogg_b64, 'base64'), mimetype: 'audio/ogg; codecs=opus', ptt: true })
        done.push(it.id)
      } catch (e) { console.log('send error', String(e).slice(0, 200)); break }
    }
    if (done.length) await post('/whatsapp/ack', { ids: done })
  } catch {}
}, 2000)
