import test from 'node:test';
import assert from 'node:assert/strict';
import { submitPostalMessage, PostalSubmissionError } from '../lib/postal-transport.mjs';

const env = {
  POSTAL_BASE_URL: 'https://postal.aiajacademy.io',
  POSTAL_TRANSPORT_MODE: 'test',
  POSTAL_API_KEY_ACADEMY: 'academy-test-secret',
  POSTAL_API_KEY_SOLUTIONS: 'solutions-test-secret',
  POSTAL_API_KEY_RADIO: 'radio-test-secret',
  POSTAL_RADIO_FROM: 'outreach@aiajacademy.io',
};
const base = { recipient: 'test@example.invalid', subject: 'Integration test', text: 'Test content', campaignId: 'aws-bridge', ownerAuthorized: true };

for (const [workspace, from, credential] of [
  ['academy', 'outreach@aiajacademy.io', env.POSTAL_API_KEY_ACADEMY],
  ['solutions', 'outreach@aiaj.solutions', env.POSTAL_API_KEY_SOLUTIONS],
  ['radio', 'outreach@aiajacademy.io', env.POSTAL_API_KEY_RADIO],
]) {
  test(`${workspace} submits to HTTPS, correctly scoped`, async () => {
    let calls = 0;
    const result = await submitPostalMessage({ ...base, workspace }, { env, fetchImpl: async (url, request) => {
      calls += 1;
      assert.equal(url, 'https://postal.aiajacademy.io/api/v1/send/message');
      assert.equal(request.headers['X-Server-API-Key'], credential);
      assert.equal(request.method, 'POST');
      const body = JSON.parse(request.body);
      assert.equal(body.from, from);
      assert.deepEqual(body.to, ['test@example.invalid']);
      assert.equal(body.tag, `${workspace}-aws-bridge`);
      return { ok: true, status: 200, json: async () => ({ status: 'success', data: { message_id: 'test-id' } }) };
    } });
    assert.equal(calls, 1);
    assert.equal(result.state, 'postal_api_accepted');
    assert.equal(result.deliveryStatus, 'unverified');
  });
}

test('rejects live recipient during test mode without calling API', async () => {
  await assert.rejects(submitPostalMessage({ ...base, workspace: 'academy', recipient: 'real@example.com' }, {env, fetchImpl: () => { throw Error('should not be called'); }}), { code: 'test_recipient_required' });
});
test('no live traffic without both switches', async () => {
  await assert.rejects(submitPostalMessage({ ...base, workspace: 'academy' }, {env: { ...env, POSTAL_TRANSPORT_MODE: 'live' }, fetchImpl: () => { throw Error('should not be called'); }}), { code: 'live_disabled' });
});
test('requires explicit owner authorization', async () => {
  await assert.rejects(submitPostalMessage({ ...base, workspace: 'academy', ownerAuthorized: false }, {env}), { code: 'not_authorized' });
});
test('disabled by default', async () => {
  const { POSTAL_TRANSPORT_MODE, ...defaultEnv } = env;
  await assert.rejects(submitPostalMessage({ ...base, workspace: 'academy' }, {env: defaultEnv}), { code: 'transport_disabled' });
});
test('Postal rejection is not reported as accepted', async () => {
  await assert.rejects(submitPostalMessage({ ...base, workspace: 'academy' }, {env, fetchImpl: async () => ({ ok:true, status:200, json:async()=>({status:'error',data:{code:'NoRecipients'}}) }) }), {code:'NoRecipients'});
});
test('network errors do not leak the API key', async () => {
  try {
    await submitPostalMessage({ ...base, workspace: 'academy' }, {env, fetchImpl: async () => { throw new Error('bad network'); }});
    assert.fail('Expected error');
  } catch (error) {
    assert.ok(error instanceof PostalSubmissionError);
    assert.equal(error.code, 'network_error');
    assert.ok(!error.message.includes(env.POSTAL_API_KEY_ACADEMY));
  }
});
