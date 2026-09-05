import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import http from 'node:http';
import test from 'node:test';
import { callPdfUtility } from './pdf-client';

test('interrupted PDF utility responses reject without an unhandled stream error', async t => {
  const response = new EventEmitter();
  const request = Object.assign(new EventEmitter(), { end() {}, destroy() {} });
  t.mock.method(http, 'request', (_options: unknown, callback: (value: unknown) => void) => {
    callback(response);
    return request;
  });
  const pending = callPdfUtility('profile', Buffer.from('%PDF-1.7'));
  const rejected = assert.rejects(pending, /connection reset/);
  response.emit('error', new Error('connection reset'));
  await rejected;
});

test('aborted PDF responses settle the request instead of hanging', async t => {
  const response = new EventEmitter();
  const request = Object.assign(new EventEmitter(), { end() {}, destroy() {} });
  t.mock.method(http, 'request', (_options: unknown, callback: (value: unknown) => void) => {
    callback(response);
    return request;
  });
  const pending = callPdfUtility('profile', Buffer.from('%PDF-1.7'));
  const rejected = assert.rejects(pending, /interrupted/);
  response.emit('aborted');
  await rejected;
});
