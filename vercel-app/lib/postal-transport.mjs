/**
 * Postal API transport for AIAJ Outreach Engine.
 * The caller must perform ledger claims, suppression/verification checks,
 * and campaign authorization before calling this transport.
 */
const IDENTITY = Object.freeze({
  academy: { key: 'POSTAL_API_KEY_ACADEMY', sender: 'outreach@aiajacademy.io' },
  solutions: { key: 'POSTAL_API_KEY_SOLUTIONS', sender: 'outreach@aiaj.solutions' },
  radio: { key: 'POSTAL_API_KEY_RADIO', sender: null },
});

export class PostalSubmissionError extends Error {
  constructor(message, details = {}) {
    super(message);
    this.name = 'PostalSubmissionError';
    this.code = details.code || 'postal_submission_error';
    this.httpStatus = details.httpStatus || null;
  }
}

/**
 * No implicit live sending. Test mode accepts only test@example.invalid.
 * Live mode requires two independent environment switches.
 */
export async function submitPostalMessage({ workspace, recipient, subject, text, campaignId, ownerAuthorized }, { env = process.env, fetchImpl = globalThis.fetch } = {}) {
  if (!ownerAuthorized) throw new PostalSubmissionError('Campaign owner authorization required', { code: 'not_authorized' });
  const identity = IDENTITY[workspace];
  if (!identity) throw new PostalSubmissionError('Unrecognized workspace', { code: 'invalid_workspace' });
  const mode = env.POSTAL_TRANSPORT_MODE || 'disabled';
  if (mode !== 'test' && mode !== 'live') throw new PostalSubmissionError('Postal transport is disabled', { code: 'transport_disabled' });
  if (mode === 'live' && !(env.POSTAL_LIVE_ENABLED === 'true' && env.AIAJ_LIVE_ENABLED === 'true')) {
    throw new PostalSubmissionError('Live sending has not been enabled', { code: 'live_disabled' });
  }
  if (typeof recipient !== 'string' || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(recipient) || /[,;\s]/.test(recipient)) {
    throw new PostalSubmissionError('One valid recipient required', { code: 'invalid_recipient' });
  }
  if (mode === 'test' && !recipient.toLowerCase().endsWith('@example.invalid')) {
    throw new PostalSubmissionError('Test mode only accepts addresses at example.invalid', { code: 'test_recipient_required' });
  }
  if (!subject || typeof subject !== 'string' || typeof text !== 'string' || !text.trim()) {
    throw new PostalSubmissionError('Subject and text body required', { code: 'invalid_message' });
  }
  if (!campaignId || typeof campaignId !== 'string') throw new PostalSubmissionError('Campaign ID required', { code: 'invalid_campaign' });
  const sender = workspace === 'radio' ? env.POSTAL_RADIO_FROM : identity.sender;
  if (!sender || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(sender)) throw new PostalSubmissionError('Sender not configured', { code: 'sender_missing' });
  const apiKey = env[identity.key];
  if (!apiKey) throw new PostalSubmissionError('Postal API credential missing', { code: 'credential_missing' });
  let endpoint;
  try {
    endpoint = new URL('/api/v1/send/message', env.POSTAL_BASE_URL);
    if (endpoint.protocol !== 'https:') throw new Error('HTTPS required');
  } catch {
    throw new PostalSubmissionError('Invalid Postal HTTPS endpoint', { code: 'invalid_endpoint' });
  }
  const tag = `${workspace}-${campaignId}`.replace(/[^a-zA-Z0-9_-]/g, '-').slice(0,100);
  let response;
  try {
    response = await fetchImpl(endpoint.toString(), {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'X-Server-API-Key': apiKey },
      body: JSON.stringify({ from: sender, to: [recipient], subject, plain_body: text, tag }),
      signal: AbortSignal.timeout(15000),
    });
  } catch {
    throw new PostalSubmissionError('Postal API network request failed', { code: 'network_error' });
  }
  let body;
  try { body = await response.json(); } catch { throw new PostalSubmissionError('Invalid Postal API response', { code: 'invalid_response', httpStatus: response.status }); }
  if (!response.ok || body.status !== 'success' || !body.data?.message_id) {
    throw new PostalSubmissionError('Postal API did not accept message', { code: body.data?.code || 'postal_rejected', httpStatus: response.status });
  }
  return {
    state: 'postal_api_accepted',
    workspace,
    campaignId,
    messageId: body.data.message_id,
    recipient,
    deliveryStatus: 'unverified',
  };
}
