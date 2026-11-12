import assert from 'node:assert/strict';
import test from 'node:test';
import { apiBaseFor, requestJSON } from '../api-client.js';

test('Live Server uses the Python API while the main website keeps its own origin', () => {
  assert.equal(apiBaseFor(new URL('http://127.0.0.1:5500/')), 'http://127.0.0.1:8000');
  assert.equal(apiBaseFor(new URL('http://localhost:5501/')), 'http://localhost:8000');
  assert.equal(apiBaseFor(new URL('http://127.0.0.1:8000/')), '');
  assert.equal(apiBaseFor(new URL('https://example.com/')), '');
});

test('requests JSON from the selected backend and preserves cancellation', async () => {
  const controller = new AbortController();
  const result = await requestJSON('/api/search?q=cat', {
    base: 'http://127.0.0.1:8000', signal: controller.signal,
    fetchImpl: async (url, options) => {
      assert.equal(url, 'http://127.0.0.1:8000/api/search?q=cat');
      assert.equal(options.signal, controller.signal);
      assert.equal(options.headers.Accept, 'application/json');
      return Response.json({ results: [] });
    },
  });
  assert.deepEqual(result, { results: [] });
});

test('HTML responses never escape as raw JSON parser errors', async () => {
  for (const status of [200, 404]) {
    await assert.rejects(requestJSON('/api/search', {
      fetchImpl: async () => new Response('<!DOCTYPE html><html>Static preview</html>', { status, headers: { 'content-type': 'text/html' } }),
    }), error => error.code === 'invalid-response' && error.status === status && error.message.includes('8000') && !error.message.includes('Unexpected token'));
  }
});

test('invalid JSON has a readable error and HTTP API errors keep their details', async () => {
  await assert.rejects(requestJSON('/api/search', {
    fetchImpl: async () => new Response('invalid', { headers: { 'content-type': 'application/json' } }),
  }), error => error.code === 'invalid-response' && !(error instanceof SyntaxError));
  await assert.rejects(requestJSON('/api/search', {
    fetchImpl: async () => Response.json({ detail: 'Tìm kiếm chưa sẵn sàng.' }, { status: 503 }),
  }), error => error.code === 'http' && error.status === 503 && error.message === 'Tìm kiếm chưa sẵn sàng.');
});

test('connection failures explain startup and aborted requests stay silent', async () => {
  await assert.rejects(requestJSON('/api/catalog', {
    fetchImpl: async () => { throw new TypeError('Failed to fetch'); },
  }), error => error.code === 'connection' && error.message.includes('start-web.ps1'));
  const aborted = new DOMException('Aborted', 'AbortError');
  await assert.rejects(requestJSON('/api/catalog', {
    fetchImpl: async () => { throw aborted; },
  }), error => error === aborted);
});
