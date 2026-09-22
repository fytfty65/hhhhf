import assert from 'node:assert/strict';
import test from 'node:test';
import { webPushCapability } from './webPush.ts';

test('web push capability degrades safely without browser APIs', () => {
  assert.equal(webPushCapability(), 'unsupported');
});
