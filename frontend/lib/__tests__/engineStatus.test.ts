import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { runEngineStatusAssertions } from '../engineStatus';

describe('engineStatus normalisation (Task #127)', () => {
  it('handles every documented payload shape without errors', () => {
    const failures = runEngineStatusAssertions();
    assert.deepEqual(failures, [], failures.join('\n'));
  });
});
