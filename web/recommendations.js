export const isFinishedRun = (run) => ['completed', 'failed'].includes(run.status);

const delay = (milliseconds, signal) => new Promise((resolve) => {
  if (signal?.aborted) { resolve(); return; }
  const finish = () => {
    clearTimeout(timer);
    signal?.removeEventListener('abort', finish);
    resolve();
  };
  const timer = setTimeout(finish, milliseconds);
  signal?.addEventListener('abort', finish, { once: true });
});

export async function pollRecommendation({
  run, load, onUpdate, signal, intervalMs = 2000, timeoutMs = 12 * 60 * 1000,
  now = () => performance.now(), wait = delay,
}) {
  const started = now();
  while (!signal?.aborted && !isFinishedRun(run)) {
    if (now() - started >= timeoutMs) return { status: 'paused', run };
    await wait(intervalMs, signal);
    if (signal?.aborted) break;
    const updated = await load(run.id, signal);
    if (signal?.aborted) break;
    run = updated;
    onUpdate(run);
  }
  return { status: signal?.aborted ? 'cancelled' : 'finished', run };
}
