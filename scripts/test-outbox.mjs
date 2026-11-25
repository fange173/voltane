import assert from 'node:assert/strict';
import test from 'node:test';
import { state } from '../app/static/js/state.js';

test('逐笔确认出队：并发新增、疑似重复、账号切换和明确确认', async () => {
  const rows = new Map();
  const el = () => ({ appendChild() {}, querySelectorAll: () => [], classList: { add() {} }, remove() {} });
  globalThis.window = { addEventListener() {} };
  globalThis.document = { addEventListener() {}, createElement: el, body: el() };
  const { confirmDuplicate, enqueue, flush, pending } = await import('../app/static/js/outbox.js');
  globalThis.localStorage = { get length() { return rows.size; }, key: i => [...rows.keys()][i],
    getItem: k => rows.get(k) ?? null, setItem: (k, v) => rows.set(k, v), removeItem: k => rows.delete(k) };
  const timer = globalThis.setTimeout;
  globalThis.setTimeout = () => 0;
  const add = id => enqueue({ url: '/api/records', vehicle_id: 1, body: { vehicle_id: 1, client_id: id } });
  const saved = id => Response.json({ record: { id: 12, vehicle_id: 1, client_id: id } });
  try {
    state.user = { id: 7 };
    add('a');
    let respond;
    globalThis.fetch = () => new Promise(resolve => { respond = resolve; });
    const flight = flush();
    add('b');
    respond(saved('a'));
    await flight;
    assert.deepEqual(pending().map(it => it.body.client_id), ['b']);
    state.user = { id: 8 };
    assert.deepEqual(pending(), []);
    state.user = { id: 7 };
    globalThis.fetch = async () => Response.json({ detail: { code: 'duplicate', message: '可能重复' } }, { status: 409 });
    await flush();
    assert.equal(pending()[0].duplicate, true);
    const sent = [];
    globalThis.fetch = async (_, opts) => { sent.push(JSON.parse(opts.body)); return saved('b'); };
    await flush();
    assert.equal(sent.length, 0);
    await confirmDuplicate('b');
    assert.deepEqual(sent, [{ vehicle_id: 1, client_id: 'b', force: true }]);
    assert.deepEqual(pending(), []);
    add('c');
    globalThis.fetch = async () => saved('not-c');
    await flush();
    assert.equal(pending()[0].body.client_id, 'c');
    globalThis.fetch = async () => { throw Error('offline'); };
    await flush();
    assert.equal(pending().length, 1);
  } finally { globalThis.setTimeout = timer; }
});
