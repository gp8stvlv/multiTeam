import assert from 'node:assert/strict';
import test from 'node:test';

let moduleVersion = 0;
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
const until = async (predicate) => {
  const deadline = Date.now() + 5000;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error('UI did not reach the expected state');
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
};

async function browser(t, request) {
  const elements = new Map();
  const documentEvents = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, {
      value: '', innerHTML: '', textContent: '', disabled: false, dataset: {}, events: new Map(),
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener(type, handler) { this.events.set(type, handler); },
      focus() {}, scrollIntoView() {},
    });
    return elements.get(selector);
  };
  const originals = new Map(['document', 'window', 'location', 'fetch', 'setTimeout'].map((key) => [key, globalThis[key]]));
  globalThis.document = { querySelector: element, querySelectorAll: () => [], addEventListener: (type, handler) => documentEvents.set(type, handler) };
  globalThis.window = { scrollTo() {} };
  globalThis.location = { hash: '' };
  const timeout = originals.get('setTimeout');
  globalThis.setTimeout = (callback, milliseconds) => {
    const timer = timeout(callback, milliseconds);
    if (milliseconds === 4200) timer.unref();
    return timer;
  };
  globalThis.fetch = async (url, options) => {
    let payload;
    if (url.endsWith('/auth/login')) payload = { access_token: 'user-jwt', user: { email: 'user@example.test', role: 'user' } };
    else if (url.includes('/restaurants?')) payload = { items: [{ id: 'restaurant-1', name: 'Restaurant', is_demo: false }], total: 1 };
    else if (url.includes('/me/feedback?')) payload = { items: [] };
    else payload = await request(url, options);
    return new Response(JSON.stringify(payload), { headers: { 'Content-Type': 'application/json' } });
  };
  t.after(() => { for (const [key, original] of originals) globalThis[key] = original; });
  await import(`./app.js?test=${moduleVersion++}`);
  const click = (target) => documentEvents.get('click')({ target: { closest: () => ({ dataset: {}, ...target }) } });
  const submit = (selector) => element(selector).events.get('submit')({ preventDefault() {} });
  element('#auth-api-key').value = 'test-team-key';
  element('#auth-email').value = 'user@example.test';
  element('#auth-password').value = 'password';
  await submit('#auth-form');
  element('#query-input').value = 'Обед';
  return { element, click, submit };
}

test('ignores duplicate submissions and an accepted response arriving after New search', async (t) => {
  const accepted = deferred();
  let posts = 0;
  const ui = await browser(t, async (url, options) => {
    assert.ok(url.endsWith('/recommendations'));
    assert.equal(options.headers['X-API-Key'], 'test-team-key');
    assert.equal(options.headers.Authorization, 'Bearer user-jwt');
    posts++;
    return accepted.promise;
  });
  ui.click({ dataset: { mode: 'meal_plan' } });
  const first = ui.submit('#search-form');
  await until(() => posts === 1);
  await ui.submit('#search-form');
  assert.equal(posts, 1);
  ui.click({ id: 'clear-results' });
  const content = ui.element('#results-content').innerHTML;
  accepted.resolve({ id: 'old-run', status: 'queued' });
  await first;
  assert.equal(ui.element('#results-content').innerHTML, content);
  assert.equal(ui.element('#search-submit').disabled, false);
});

test('a slow agent result cannot overwrite a newer direct menu search', async (t) => {
  const oldResult = deferred();
  let polling = false;
  const ui = await browser(t, async (url) => {
    if (url.endsWith('/recommendations')) return { id: 'old-run', status: 'queued' };
    if (url.endsWith('/recommendations/old-run')) { polling = true; return oldResult.promise; }
    assert.ok(url.endsWith('/search'));
    return { items: [{ item: { id: 'fresh-dish', name: 'Свежий результат', nutrition: {} }, restaurant: { name: 'Restaurant' } }] };
  });
  ui.click({ dataset: { mode: 'meal_plan' } });
  const oldSearch = ui.submit('#search-form');
  await until(() => polling);
  ui.click({ dataset: { mode: 'dish_search' } });
  await ui.submit('#search-form');
  assert.match(ui.element('#results-content').innerHTML, /Свежий результат/);
  oldResult.resolve({ id: 'old-run', status: 'failed', error: { message: 'Устаревшая ошибка' } });
  await oldSearch;
  assert.match(ui.element('#results-content').innerHTML, /Свежий результат/);
  assert.doesNotMatch(ui.element('#results-content').innerHTML, /Устаревшая ошибка/);
});

test('Resume retries a GET for the existing job after a network failure', async (t) => {
  let posts = 0;
  let gets = 0;
  const ui = await browser(t, async (url) => {
    if (url.endsWith('/recommendations')) { posts++; return { id: 'existing-run', status: 'running' }; }
    assert.ok(url.endsWith('/recommendations/existing-run'));
    gets++;
    if (gets === 1) throw new Error('Network down');
    return { id: 'existing-run', status: 'completed', result: { outcome: 'no_matches', warnings: [] } };
  });
  ui.click({ dataset: { mode: 'meal_plan' } });
  await ui.submit('#search-form');
  assert.match(ui.element('#results-content').innerHTML, /Продолжить ожидание/);
  assert.equal(ui.element('#search-submit').disabled, true);
  ui.click({ id: 'resume-run' });
  await until(() => ui.element('#results-content').innerHTML.includes('Подходящих вариантов нет'));
  assert.equal(posts, 1);
  assert.equal(gets, 2);
  assert.equal(ui.element('#search-submit').disabled, false);
});
