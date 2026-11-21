import nodemailer from 'nodemailer';
import { submitPostalMessage, PostalSubmissionError } from '../lib/postal-transport.mjs';

// Preserve the production ledger API. Do not report API acceptance as inbox delivery.
async function ledger(action, b) {
  const response = await fetch(process.env.SUPABASE_LEDGER_URL, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      apikey: process.env.SUPABASE_ANON_KEY,
      authorization: `Bearer ${process.env.SUPABASE_ANON_KEY}`,
      'x-aiaj-admin-token': process.env.AIAJ_ADMIN_TOKEN,
    },
    body: JSON.stringify({ action, ...b }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(`ledger_${response.status}`);
  return result;
}

const WORKSPACES = new Set(['academy', 'solutions', 'radio']);
const MAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
function isPostal(mode) { return mode === 'test' || mode === 'live'; }
function validCampaign(id) { return typeof id === 'string' && /^[a-zA-Z0-9_-]{5,100}$/.test(id); }

function postalPreflight({ body, items, mode }) {
  if (!WORKSPACES.has(body.workspace)) return 'postal_workspace_required';
  if (body.ownerAuthorized !== true) return 'postal_owner_authorization_required';
  if (mode === 'live' && !(process.env.POSTAL_LIVE_ENABLED === 'true' && process.env.AIAJ_LIVE_ENABLED === 'true')) return 'postal_live_disabled';
  for (const item of items) {
    const recipient = String(item?.recipient || item?.email || item?.to || '').trim().toLowerCase();
    if (!MAIL_REGEX.test(recipient) || /[,;\s]/.test(recipient)) return 'invalid_recipient';
    if (item.workspace != null && item.workspace !== body.workspace) return 'workspace_mismatch';
    if (!item.subject || !(item.text || item.body)) return 'invalid_message';
    if (mode === 'test' && !recipient.endsWith('@example.invalid')) return 'postal_test_recipient_required';
    // Live contact verification is enforced by the Supabase verification RPC
    // before the campaign ledger claim; never trust item-supplied verification.
  }
  if (!process.env.POSTAL_BASE_URL) return 'postal_base_url_missing';
  const credential = { academy: 'POSTAL_API_KEY_ACADEMY', solutions: 'POSTAL_API_KEY_SOLUTIONS', radio: 'POSTAL_API_KEY_RADIO' }[body.workspace];
  if (!process.env[credential]) return 'postal_credential_missing';
  return null;
}

export default async function handler(req, res) {
  const token = process.env.AIAJ_ADMIN_TOKEN;
  if (!token) return res.status(503).json({ error: 'admin_token_not_configured' });
  if (req.headers.authorization !== `Bearer ${token}` && req.headers['x-aiaj-admin-token'] !== token) {
    return res.status(401).json({ error: 'unauthorized' });
  }

  if (req.method === 'GET') {
    try { return res.json(await ledger('stats', { campaignId: req.query.campaignId || req.query.id })); }
    catch { return res.status(503).json({ error: 'ledger_unavailable' }); }
  }
  if (req.method !== 'POST') return res.status(405).end();

  let body = req.body || {};
  if (typeof body === 'string') { try { body = JSON.parse(body); } catch { return res.status(400).json({ error: 'invalid_json' }); } }
  if (!body || typeof body !== 'object') return res.status(400).json({ error: 'invalid_json' });
  const items = body.items || body.recipients || body.messages || body.payload?.items || [];
  const campaignId = body.campaignId || body.campaign_id || body.id || body.payload?.campaignId;
  if (!Array.isArray(items) || items.length < 1 || items.length > 50 || !validCampaign(campaignId)) {
    return res.status(400).json({ error: 'invalid_campaign', itemCount: Array.isArray(items) ? items.length : null });
  }

  // Disabled = unchanged legacy SMTP route. Test/live explicitly selects Postal.
  const mode = process.env.POSTAL_TRANSPORT_MODE || 'disabled';
  if (mode !== 'disabled' && !isPostal(mode)) return res.status(503).json({ error: 'invalid_transport_mode' });
  const postal = isPostal(mode);
  if (postal) {
    const issue = postalPreflight({ body, items, mode });
    if (issue) return res.status(issue.endsWith('missing') ? 503 : 400).json({ error: issue });
  }

  const results = [];
  for (const item of items) {
    const recipient = String(item?.recipient || item?.email || item?.to || '').trim().toLowerCase();
    const text = item?.text || item?.body;
    if (postal && mode === 'live') {
      let authorized;
      try {
        authorized = await ledger('verify', { campaignId, email: recipient });
      } catch {
        results.push({ recipient, status: 'held_verification_unavailable' });
        continue;
      }
      if (authorized.verified !== true) {
        results.push({ recipient, status: 'held_unverified' });
        continue;
      }
    }
    try {
      const claimed = await ledger('claim', { campaignId, email: recipient, subject: item?.subject, text });
      if (claimed.status !== 'reserved') {
        results.push({ recipient, status: claimed.status });
        continue;
      }
      await ledger('state', { campaignId, email: recipient, state: 'sending' });

      if (postal) {
        let posted;
        try {
          posted = await submitPostalMessage({
            workspace: body.workspace, recipient, subject: item.subject, text,
            campaignId, ownerAuthorized: body.ownerAuthorized === true,
          });
        } catch (error) {
          // A network timeout may occur AFTER Postal has accepted a message.
          // Never blindly retry a claimed message.
          const knownRejected = error instanceof PostalSubmissionError && ['postal_rejected', 'NoRecipients', 'UnauthenticatedFromAddress'].includes(error.code);
          const state = knownRejected ? 'postal_rejected' : 'postal_outcome_unknown';
          await ledger('state', { campaignId, email: recipient, state }).catch(() => {});
          results.push({ recipient, status: state, error: error?.code || 'postal_error' });
          continue;
        }
        try {
          await ledger('state', { campaignId, email: recipient, state: 'postal_api_accepted', messageId: posted.messageId });
          results.push({ recipient, status: 'postal_api_accepted', messageId: posted.messageId, deliveryStatus: 'unverified' });
        } catch {
          // Message is accepted by Postal; state persistence failed. Never resubmit automatically.
          results.push({ recipient, status: 'postal_api_accepted_ledger_update_failed', messageId: posted.messageId, deliveryStatus: 'unverified' });
        }
        continue;
      }

      // Unmodified fallback for the existing Namecheap SMTP route.
      const port = Number(process.env.SMTP_PORT || 587);
      const smtp = nodemailer.createTransport({ host: process.env.SMTP_HOST || 'mail.privateemail.com', port, secure: port === 465, requireTLS: port !== 465, auth: { user: process.env.SMTP_USER, pass: process.env.SMTP_PASSWORD } });
      try {
        const info = await smtp.sendMail({ from: process.env.SMTP_FROM, to: recipient, subject: item.subject, text, replyTo: process.env.SMTP_REPLY_TO || process.env.SMTP_FROM });
        await ledger('state', { campaignId, email: recipient, state: 'smtp_accepted', messageId: info.messageId });
        results.push({ recipient, status: 'smtp_accepted', messageId: info.messageId, accepted: info.accepted, rejected: info.rejected });
      } catch {
        await ledger('state', { campaignId, email: recipient, state: 'outcome_unknown' }).catch(() => {});
        results.push({ recipient, status: 'outcome_unknown' });
      }
    } catch {
      results.push({ recipient, status: 'error' });
    }
  }
  return res.json({ campaignId, transport: postal ? 'postal' : 'smtp', results });
}
