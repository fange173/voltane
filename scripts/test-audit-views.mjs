import assert from 'node:assert/strict';
import { test } from 'node:test';

const noop = () => {};
globalThis.window = globalThis;
globalThis.addEventListener = noop;
globalThis.document = { addEventListener: noop, querySelector: () => null };
const storage = new Map();
globalThis.localStorage = { getItem: (k) => storage.get(k), setItem: (k, v) => storage.set(k, v) };
globalThis.location = { href: 'http://test.invalid/', pathname: '/' };
globalThis.setInterval = noop;

test('VT-012: month subtotal distinguishes unknown, zero, fees and ongoing charges', async () => {
  const { monthSummary } = await import('../app/static/js/records.js');
  const charge = (amount_cents, status = 'done') => ({ kind: 'charge', amount_cents, charge_kwh: 10, status });
  const snap = { kind: 'snapshot' };
  assert.equal(monthSummary([charge(null), charge(null), snap]), '金额未填 · 20kWh');
  assert.equal(monthSummary([charge(0), charge(0)]), '¥0.00 · 20kWh');
  assert.equal(monthSummary([charge(1250), charge(null)]), '已知 ¥12.50，1 笔未填 · 20kWh');
  assert.equal(monthSummary([{ kind: 'fuel', amount_cents: 7500, extra_fee_cents: 100, fuel_liters: 11 }]), '¥76.00 · 11L');
  assert.equal(monthSummary([charge(1000), { type: 'expense', amount_cents: 800 }, charge(2000, 'in_progress')]), '¥18.00 · 10kWh');
});

test('VT-019: tariff respects midnight and fractional minutes', async () => {
  const { tariffAmount } = await import('../app/static/js/record-form.js');
  const rates = { tou_tariff: [{ from: '12:00', to: '00:00', price: 1 }, { from: '00:00', to: '12:00', price: 2 }] };
  for (const [time, amount] of [['23:58', 8], ['23:55', 5], ['00:00', 10]]) {
    assert.equal(tariffAmount(rates, 5, `2026-11-01T${time}`, 5), amount);
  }
  rates.tou_tariff[1].price = 3;
  assert.equal(tariffAmount(rates, 1, '2026-11-01T23:59', 5), 2.6);
  assert.equal(tariffAmount(rates, 1, '2026-11-01T23:59:30', 1), 2);
});

test('VT-026: unusable combined segment never replaces the usable fuel segment', async () => {
  const { dedupeSegments } = await import('../app/static/js/records.js');
  const fuel = { energy: 'fuel', status: 'ok', start_id: 1, end_id: 2, per100: 10 };
  const unknown = { ...fuel, energy: 'combined', status: 'unknown', elec_per100: null };
  assert.deepEqual(dedupeSegments([unknown, fuel]), [unknown, fuel]);
  const usable = { ...unknown, status: 'ok', elec_per100: 5 };
  assert.deepEqual(dedupeSegments([usable, fuel]), [usable]);
});

test('VT-033: opening a row dispatches once after rebinding', async () => {
  const { bindRows } = await import('../app/static/js/records.js');
  const callbacks = [];
  const root = { addEventListener: (name, fn) => callbacks.push(fn) };
  let requests = 0;
  globalThis.fetch = () => { requests++; return new Promise(noop); };
  for (let i = 0; i < 3; i++) bindRows(root);
  for (const fn of callbacks) fn({ target: { closest: () => ({ dataset: { id: '24', type: 'record' } }) } });
  assert.equal(requests, 1);
});

test('VT-040: normalize only displayed negative zero', async () => {
  const { num, fixed } = await import('../app/static/js/util.js');
  assert.equal(num(-0.004), '0');
  assert.equal(fixed(-0.004), '0.00');
  assert.equal(num(-0.01), '-0.01');
  assert.equal(num(0.01), '0.01');
});
