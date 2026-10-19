import assert from 'node:assert/strict';
import test from 'node:test';
import { pollRecommendation } from './recommendations.js';

const queued = { id: 'existing-run', status: 'queued' };

test('keeps waiting past the old 88-second cutoff and stops on the result', async () => {
  let elapsed = 0;
  let requests = 0;
  const updates = [];
  const outcome = await pollRecommendation({
    run: queued,
    now: () => elapsed,
    wait: async (milliseconds) => { elapsed += milliseconds; },
    load: async (id) => {
      assert.equal(id, queued.id);
      requests++;
      return { id, status: elapsed >= 100000 ? 'completed' : 'running' };
    },
    onUpdate: (run) => updates.push(run.status),
  });
  assert.equal(outcome.status, 'finished');
  assert.equal(outcome.run.status, 'completed');
  assert.equal(elapsed, 100000);
  assert.equal(requests, 50);
  assert.equal(updates.at(-1), 'completed');
});

test('a late response cannot update the page after cancellation', async () => {
  const controller = new AbortController();
  const updates = [];
  const outcome = await pollRecommendation({
    run: queued, signal: controller.signal,
    wait: async () => {},
    load: async () => { controller.abort(); return { id: queued.id, status: 'completed' }; },
    onUpdate: (run) => updates.push(run),
  });
  assert.equal(outcome.status, 'cancelled');
  assert.deepEqual(updates, []);
});

test('pausing and resuming continues the same server job without recreating it', async () => {
  let elapsed = 0;
  const requested = [];
  const options = {
    run: queued, timeoutMs: 4000,
    now: () => elapsed,
    wait: async (milliseconds) => { elapsed += milliseconds; },
    load: async (id) => { requested.push(id); return { id, status: 'running' }; },
    onUpdate: () => {},
  };
  const paused = await pollRecommendation(options);
  assert.equal(paused.status, 'paused');
  const resumed = await pollRecommendation({
    ...options, run: paused.run,
    load: async (id) => { requested.push(id); return { id, status: 'completed' }; },
  });
  assert.equal(resumed.status, 'finished');
  assert.deepEqual(requested, [queued.id, queued.id, queued.id]);
});

test('failed jobs are terminal and a polling error does not fabricate a failed job', async () => {
  let calls = 0;
  const failed = await pollRecommendation({
    run: queued, wait: async () => {}, onUpdate: () => {},
    load: async () => { calls++; return { id: queued.id, status: 'failed' }; },
  });
  assert.equal(failed.status, 'finished');
  assert.equal(failed.run.status, 'failed');
  assert.equal(calls, 1);
  await assert.rejects(pollRecommendation({
    run: queued, wait: async () => {}, onUpdate: () => {},
    load: async () => { throw new Error('Connection lost'); },
  }), /Connection lost/);
});
