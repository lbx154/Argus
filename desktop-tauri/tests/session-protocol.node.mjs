import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { FIXTURE_AUTH, startSessionFixture } from './session-context-fixture.mjs';

test('desktop session fixture uses the shared protocol contract, not frontend wrapper text', async () => {
  const contract = JSON.parse(await readFile(new URL('../../packages/contracts/schemas/api_protocol.json', import.meta.url), 'utf8'));
  const fixture = await startSessionFixture();
  try {
    const response = await fetch(`${fixture.origin}/api/meta`, {
      headers: { Authorization: `Bearer ${FIXTURE_AUTH}` },
    });
    assert.equal(response.status, 200);
    const metadata = await response.json();
    assert.equal(metadata.service, contract.API_SERVICE);
    assert.deepEqual(metadata.protocol, {
      name: contract.API_PROTOCOL_NAME,
      major: contract.API_PROTOCOL_MAJOR,
      minor: contract.API_PROTOCOL_MINOR,
    });
    assert.equal(metadata.snapshot_schema_version, contract.SNAPSHOT_SCHEMA_VERSION);
    assert.deepEqual(metadata.capabilities, contract.API_CAPABILITIES);
    assert.deepEqual(fixture.state.unexpected, []);
  } finally {
    await fixture.close();
  }
});
