import { profilePatch } from './profile.js';

const API_BASE = '/api/v1';
const state = { page: 'home', mode: 'dish_search', apiKey: '', token: '', email: '', role: '', authMode: 'login', demo: true, authRequired: true, restaurants: [], restaurantTotal: 0, demoCount: 0, menuItems: [], resultItems: [], feedback: new Map(), pollId: 0 };
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const money = (minor, currency = 'RUB') => minor == null ? 'Цена не указана' : new Intl.NumberFormat('ru-RU', { style: 'currency', currency, maximumFractionDigits: 0 }).format(minor / 100);
const number = (value, unit = '') => value == null ? '—' : `${Math.round(Number(value))}${unit}`;
const grams = (value) => value == null ? '—' : `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(Number(value))} г`;
const kcalText = (nutrition) => nutrition?.calories_kcal == null ? 'Калории не указаны' : `${nutrition.source === 'estimated' ? '≈ ' : ''}${number(nutrition.calories_kcal, ' ккал')}${nutrition.source === 'estimated' ? ' (оценка)' : ''}`;
const nutritionText = (nutrition) => `${kcalText(nutrition)}${nutrition?.protein_g == null && nutrition?.fat_g == null && nutrition?.carbs_g == null ? '' : ` · Б ${grams(nutrition.protein_g)} · Ж ${grams(nutrition.fat_g)} · У ${grams(nutrition.carbs_g)}`}`;
const totalKcalText = (nutrition) => nutrition?.calories_kcal == null ? '—' : `${nutrition.sources?.includes('estimated') ? '≈ ' : ''}${number(nutrition.calories_kcal, ' ккал')}${nutrition.sources?.includes('estimated') ? ' (оценка)' : ''}`;
const totalNutritionText = (nutrition) => `${totalKcalText(nutrition)}${nutrition?.protein_g == null && nutrition?.fat_g == null && nutrition?.carbs_g == null ? '' : ` · Б ${grams(nutrition.protein_g)} · Ж ${grams(nutrition.fat_g)} · У ${grams(nutrition.carbs_g)}`}`;
const cuisine = { georgian: 'Грузинская кухня', vegetarian: 'Растительная кухня', european: 'Европейская кухня', asian: 'Азиатская кухня', italian: 'Итальянская кухня' };
const icons = ['🥟', '🥗', '🍔', '🍜', '🍝', '🍽️'];
const today = () => { const date = new Date(); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`; };

class ApiError extends Error { constructor(message, status = 0) { super(message); this.status = status; } }
async function api(path, { method = 'GET', body, headers = {} } = {}) {
  const url = `${API_BASE}${path}`;
  const requestHeaders = { Accept: 'application/json', ...headers };
  if (body !== undefined) requestHeaders['Content-Type'] = 'application/json';
  if (state.apiKey) requestHeaders['X-API-Key'] = state.apiKey;
  if (state.token) requestHeaders.Authorization = `Bearer ${state.token}`;
  let response;
  try { response = await fetch(url, { method, headers: requestHeaders, body: body === undefined ? undefined : JSON.stringify(body) }); }
  catch { throw new ApiError('Не удалось связаться с API. Попробуйте ещё раз позже.'); }
  let payload = null;
  try { payload = await response.json(); } catch { /* A gateway may return an empty or non-JSON error. */ }
  if (!response.ok) {
    const detail = payload?.detail;
    const message = payload?.message || (typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item) => item.msg).filter(Boolean).join('; ') : detail?.message);
    throw new ApiError(message || `Ошибка API: HTTP ${response.status}`, response.status);
  }
  return payload;
}

function setConnection(mode) {
  const pill = $('#connection-pill'); pill.className = `connection-pill ${mode}`;
  pill.innerHTML = `<span class="status-dot"></span> ${mode === 'online' ? 'API подключён' : mode === 'auth' ? 'Нужен вход' : 'Ошибка API'}`;
  $('#footer-source').textContent = mode === 'online' ? 'Данные загружены из API' : mode === 'auth' ? 'Войдите для доступа к каталогу API' : 'Каталог API не загружен';
  $('#auth-banner').classList.toggle('hidden', mode !== 'auth');
}
function toast(message) { const element = $('#toast'); element.textContent = message; element.classList.remove('hidden'); clearTimeout(toast.timer); toast.timer = setTimeout(() => element.classList.add('hidden'), 4200); }
function empty(title, message, action = '') { return `<div class="empty-state"><strong>${escapeHtml(title)}</strong>${escapeHtml(message)}${action}</div>`; }
function errorBox(message) { return `<div class="notice-box error-box"><strong>Не получилось загрузить данные</strong>${escapeHtml(message)}</div>`; }
function feedbackButtons(dishId, name) {
  if (!dishId) return '';
  const current = state.feedback.get(dishId);
  return `<span class="feedback-actions" aria-label="Оценка блюда">${[['like', '♡ Нравится'], ['dislike', '✕ Не нравится']].map(([rating, label]) => `<button type="button" class="feedback-button${current === rating ? ' selected' : ''}" data-feedback-dish="${escapeHtml(dishId)}" data-feedback-rating="${rating}" aria-label="${escapeHtml(label + ': ' + name)}" aria-pressed="${current === rating}">${label}</button>`).join('')}</span>`;
}
function syncFeedbackButtons() {
  document.querySelectorAll('[data-feedback-dish]').forEach((button) => {
    const selected = state.feedback.get(button.dataset.feedbackDish) === button.dataset.feedbackRating;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
}
async function loadFeedback() {
  if (!state.token) return;
  const token = state.token;
  try {
    const page = await api('/me/feedback?limit=500');
    if (state.token !== token) return;
    state.feedback = new Map((page.items || []).map((item) => [item.dish_id, item.rating]));
    syncFeedbackButtons();
  } catch (error) { toast(`Не удалось загрузить оценки: ${error.message}`); }
}
async function rateDish(dishId, rating, button) {
  if (!state.token) { openModal('auth-modal'); toast('Войдите, чтобы оценивать блюда.'); return; }
  if (state.demo) { toast('Оценки доступны после подключения API.'); return; }
  if (state.feedback.get(dishId) === rating) { toast('Такая оценка уже сохранена.'); return; }
  button.disabled = true;
  const token = state.token;
  try {
    const saved = await api('/me/feedback', { method: 'POST', body: { dish_id: dishId, rating } });
    if (state.token !== token) return;
    state.feedback.set(saved.dish_id, saved.rating);
    syncFeedbackButtons();
    toast(saved.rating === 'like' ? 'Блюдо понравилось — учтём в рекомендациях.' : 'Блюдо не понравилось — учтём в рекомендациях.');
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}
function showPage(page) {
  if (!['home', 'restaurants', 'diary', 'profile'].includes(page)) return;
  state.page = page;
  document.querySelectorAll('.page').forEach((item) => item.classList.toggle('active', item.id === `page-${page}`));
  document.querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.page === page));
  $('.sidebar').classList.remove('open');
  location.hash = page;
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (page === 'diary') loadDiary();
  if (page === 'profile') loadProfile();
}
function renderRestaurants() {
  const cards = state.restaurants.map((restaurant, index) => `<button class="restaurant-card" data-restaurant="${escapeHtml(restaurant.id)}"><div class="restaurant-visual"><span>${restaurant.icon || icons[index % icons.length]}</span></div><div class="restaurant-body"><div class="restaurant-meta">${escapeHtml(cuisine[restaurant.cuisines?.[0]] || restaurant.cuisines?.[0] || 'Ресторан')}</div><h3>${escapeHtml(restaurant.name)}</h3><p>${escapeHtml(restaurant.address)}</p><div class="restaurant-foot"><span>Смотреть меню</span><strong>Открыть ↗</strong></div></div></button>`);
  const noReal = state.demo
    ? empty('Каталог пока не загружен', state.authRequired ? 'Войдите, чтобы получить данные из API.' : 'Проверьте подключение к API.')
    : empty('Ресторанов пока нет', 'Каталог ещё не наполнен.');
  $('#home-restaurants').innerHTML = cards.slice(0, 3).join('') || noReal;
  $('#all-restaurants').innerHTML = cards.join('') || noReal;
}
async function loadRestaurants() {
  try {
    const first = await api('/restaurants?limit=100&offset=0');
    const restaurants = [...(first.items || [])];
    const total = first.total ?? restaurants.length;
    while (restaurants.length < total) {
      const page = await api(`/restaurants?limit=100&offset=${restaurants.length}`);
      if (!page.items?.length) throw new ApiError('Каталог API вернул неполный список ресторанов.');
      restaurants.push(...page.items);
    }
    state.demoCount = restaurants.filter((restaurant) => restaurant.is_demo).length;
    state.restaurants = restaurants.filter((restaurant) => !restaurant.is_demo);
    state.restaurantTotal = total;
    state.demo = false; state.authRequired = false; setConnection('online');
    state.menuItems = []; $('#menu-section').classList.add('hidden');
  } catch (error) {
    if (error.status === 401) {
      state.token = ''; state.apiKey = ''; state.email = ''; state.role = ''; state.feedback = new Map(); $('#account-label').textContent = 'Войти';
      state.restaurants = []; state.restaurantTotal = 0; state.demoCount = 0; state.demo = true; state.authRequired = true; setConnection('auth');
      renderRestaurants();
      return;
    }
    if (error.status && ![502, 503, 504].includes(error.status)) {
      state.restaurants = []; state.restaurantTotal = 0; state.demoCount = 0; state.demo = true; state.authRequired = false; setConnection('error');
      $('#home-restaurants').innerHTML = errorBox(error.message);
      $('#all-restaurants').innerHTML = errorBox(error.message);
      return;
    }
    state.restaurants = []; state.restaurantTotal = 0; state.demoCount = 0; state.demo = true; state.authRequired = false; setConnection('error');
    toast('Backend недоступен: каталог API не загружен.');
  }
  renderRestaurants();
}
function renderMenuItem(item) {
  const ingredients = item.ingredients?.join(', ') || item.description || 'Состав не загружен';
  return `<article class="menu-item"><div><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(ingredients)} · ${escapeHtml(nutritionText(item.nutrition))} / порция</p></div><span class="menu-price">${escapeHtml(money(item.price_minor, item.currency || 'RUB'))}</span><div class="item-actions">${feedbackButtons(item.dish_id, item.name)}<button class="small-button" data-add-diary="${escapeHtml(item.id || '')}" ${state.demo ? `disabled title="${state.authRequired ? 'Войдите, чтобы загружать блюда из API' : 'Дневник доступен при запущенном API'}"` : ''}>+ В дневник</button></div></article>`;
}
async function openRestaurant(id) {
  const restaurant = state.restaurants.find((item) => item.id === id); if (!restaurant) return;
  showPage('restaurants');
  const section = $('#menu-section'); section.classList.remove('hidden');
  section.innerHTML = '<div class="notice-box">Загружаем меню…</div>';
  try {
    const data = await api(`/restaurants/${encodeURIComponent(id)}/menu`);
    state.menuItems = data.items || [];
    section.innerHTML = `<div class="menu-header"><div><span class="section-kicker">МЕНЮ РЕСТОРАНА</span><h2>${escapeHtml(restaurant.name)}</h2><p>${escapeHtml(restaurant.address)}</p></div><button class="outline-button" id="close-menu">Закрыть ×</button></div><div class="menu-list">${state.menuItems.length ? state.menuItems.map((item) => renderMenuItem({ ...item, is_demo: restaurant.is_demo })).join('') : empty('Меню ещё нет', 'У этого ресторана пока нет опубликованного меню.')}</div>`;
  } catch (error) { section.innerHTML = errorBox(error.message); }
  section.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function showResults(html, scroll = true) { $('#results-section').classList.remove('hidden'); $('#results-content').innerHTML = html; if (scroll) $('#results-section').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
function constraints() {
  const result = {};
  const budget = Number($('#budget-input').value); const calories = Number($('#calories-input').value);
  if ($('#budget-input').value !== '' && Number.isFinite(budget)) result.budget_minor = Math.round(budget * 100);
  if ($('#calories-input').value !== '' && Number.isFinite(calories)) result.max_calories_kcal = calories;
  return result;
}
function renderHits(items, note = '') {
  state.resultItems = items.map((hit) => hit.item);
  if (!items.length) return empty('Подходящих блюд не нашлось', 'Попробуйте другой запрос или увеличьте бюджет.');
  return `<p class="result-info">${items.length} ${items.length === 1 ? 'вариант' : 'вариантов'}${note ? ` · ${escapeHtml(note)}` : ''}</p><div class="hit-list">${items.map(({ item, restaurant }) => `<article class="hit-card"><div><span class="section-kicker">${escapeHtml(restaurant?.name || 'Ресторан')}</span><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.ingredients?.join(', ') || '')} · ${escapeHtml(nutritionText(item.nutrition))} / порция</p></div><span class="menu-price">${escapeHtml(money(item.price_minor, item.currency || 'RUB'))}</span><div class="item-actions">${feedbackButtons(item.dish_id, item.name)}<button class="small-button" data-add-diary="${escapeHtml(item.id || '')}" ${state.demo ? `disabled title="${state.authRequired ? 'Войдите, чтобы загружать блюда из API' : 'Дневник доступен при запущенном API'}"` : ''}>+ В дневник</button></div></article>`).join('')}</div>`;
}
function renderRun(run) {
  if (run.status === 'failed') return errorBox(run.error?.message || 'Подбор завершился ошибкой.');
  if (run.status !== 'completed') return `<div class="notice-box"><strong>Подбираем варианты…</strong>Задание ${escapeHtml(run.status)}. Результат появится автоматически.</div>`;
  const result = run.result;
  if (!result) return empty('Нет результата', 'Задание завершено без данных.');
  const warnings = result.warnings?.length ? `<div class="result-warnings">${result.warnings.map(escapeHtml).join(' · ')}</div>` : '';
  if (result.outcome === 'needs_clarification') return `${warnings}${empty('Нужно уточнение', result.clarifications?.map((item) => item.question || item.prompt || JSON.stringify(item)).join(' ') || 'Уточните запрос и попробуйте снова.')}`;
  if (result.outcome === 'no_matches') return `${warnings}${empty('Подходящих вариантов нет', 'Попробуйте ослабить ограничения.')}`;
  const options = (result.options || []).filter((option) => !option.restaurant?.is_demo);
  if (!options.length) return `${warnings}${empty('Реальных вариантов нет', 'В текущем каталоге не нашлось подходящих блюд из настоящих меню.')}`;
  return `${warnings}<p class="result-info">${options.length} вариантов · проверенные блюда из меню</p>${options.map((option) => `<article class="option-card"><div class="option-top"><div><span class="section-kicker">ВАРИАНТ ${escapeHtml(option.rank)}</span><h3>${escapeHtml(option.restaurant?.name || 'Ресторан')}</h3></div><span class="option-price">${escapeHtml(money(option.total_price_minor, option.currency || 'RUB'))}</span></div><p>${escapeHtml(option.restaurant?.address || '')}</p><ul>${(option.items || []).map((item) => `<li><span>${escapeHtml(item.name)} · ${escapeHtml(item.portions)} порц.</span>${feedbackButtons(item.dish_id, item.name)}</li>`).join('')}</ul><p>${escapeHtml((option.reasons || []).join(' · '))}</p><p>${escapeHtml(totalNutritionText(option.nutrition))}</p>${option.warnings?.length ? `<div class="result-warnings">${option.warnings.map(escapeHtml).join(' · ')}</div>` : ''}</article>`).join('')}`;
}
async function waitForRun(id, requestId) {
  for (let attempt = 0; attempt < 55 && state.pollId === requestId; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 1600));
    if (state.pollId !== requestId) return;
    try {
      const run = await api(`/recommendations/${encodeURIComponent(id)}`);
      showResults(renderRun(run), false);
      if (['completed', 'failed'].includes(run.status)) return;
    } catch (error) { showResults(errorBox(error.message), false); return; }
  }
  if (state.pollId === requestId) showResults(empty('Подбор ещё идёт', 'Попробуйте повторить запрос через некоторое время.'), false);
}
async function search(event) {
  event.preventDefault(); const query = $('#query-input').value.trim(); if (!query) return;
  const limits = constraints(); const button = $('#search-submit');
  if (state.demo) {
    showResults(empty('Каталог API недоступен', state.authRequired ? 'Войдите в аккаунт и повторите запрос.' : 'Проверьте подключение к API и повторите запрос.'));
    return;
  }
  if (state.mode === 'meal_plan' && !state.token) { openModal('auth-modal'); toast('Войдите, чтобы собрать приём пищи.'); return; }
  button.disabled = true; button.textContent = 'Ищем…';
  try {
    if (state.mode === 'meal_plan') {
      const run = await api('/recommendations', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: { mode: state.mode, query, constraints: limits } });
      showResults(renderRun(run));
      const requestId = ++state.pollId;
      if (!['completed', 'failed'].includes(run.status)) waitForRun(run.id, requestId);
    } else {
      const result = await api('/search', { method: 'POST', body: { query, constraints: limits, limit: 12 } });
      showResults(`${result.warnings?.length ? `<div class="result-warnings">${result.warnings.map(escapeHtml).join(' · ')}</div>` : ''}${renderHits((result.items || []).filter((hit) => !hit.restaurant?.is_demo && !hit.item?.is_demo))}`);
    }
  } catch (error) { showResults(errorBox(error.message)); }
  finally { button.disabled = false; button.innerHTML = 'Подобрать <span aria-hidden="true">→</span>'; }
}
async function addDiary(id) {
  if (!state.token) { openModal('auth-modal'); toast('Войдите, чтобы вести дневник.'); return; }
  if (state.demo) { toast('Дневник доступен после запуска backend.'); return; }
  if (!id) return;
  try {
    await api('/me/diary', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: { kind: 'menu_item', menu_item_id: id, portions: 1, consumed_at: new Date().toISOString() } });
    toast('Блюдо добавлено в дневник.');
  } catch (error) { toast(error.message); }
}
async function loadDiary() {
  const container = $('#diary-content');
  if (!state.token) { container.innerHTML = empty('Войдите в аккаунт', 'Дневник привязан к вашему профилю.', '<button class="small-button" data-open-auth>Войти</button>'); return; }
  if (state.demo) { container.innerHTML = empty('Backend пока недоступен', 'Проверьте запуск backend, чтобы вести дневник.'); return; }
  const date = $('#diary-date').value || today(); container.innerHTML = '<div class="notice-box">Загружаем дневник…</div>';
  try {
    const [summary, diary] = await Promise.all([api(`/me/daily-summary?date=${date}`), api(`/me/diary?date=${date}&limit=100`)]);
    const fields = [['Калории', summary.nutrition?.calories_kcal, 'ккал'], ['Белки', summary.nutrition?.protein_g, 'г'], ['Жиры', summary.nutrition?.fat_g, 'г'], ['Углеводы', summary.nutrition?.carbs_g, 'г']];
    container.innerHTML = `<div class="summary-grid">${fields.map(([label, value, unit]) => `<div class="summary-card"><span>${label}</span><strong>${escapeHtml(number(value))} <small>${unit}</small></strong></div>`).join('')}</div><h2 class="diary-section-title">Записи за день</h2><div class="diary-list">${diary.items?.length ? diary.items.map((entry) => `<article class="diary-entry"><div><h3>${escapeHtml(entry.snapshot?.name || 'Блюдо')}</h3><p>${escapeHtml(entry.portions)} порц. · ${escapeHtml(new Date(entry.consumed_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }))} · ${escapeHtml(totalNutritionText(entry.consumed_nutrition))}</p></div><button class="outline-button" data-delete-diary="${escapeHtml(entry.id)}">Удалить</button></article>`).join('') : empty('Пока нет записей', 'Найдите блюдо и добавьте его в дневник.')}</div>`;
    if (summary.nutrition?.sources?.includes('estimated')) container.insertAdjacentHTML('afterbegin', '<p class="result-warnings">≈ Калории в дневнике включают приблизительные оценки блюд.</p>');
  } catch (error) { container.innerHTML = errorBox(error.message); }
}
async function deleteDiary(id) {
  try { await api(`/me/diary/${encodeURIComponent(id)}`, { method: 'DELETE' }); toast('Запись удалена.'); loadDiary(); }
  catch (error) { toast(error.message); }
}
function allergyRow(allergy = {}) {
  return `<div class="allergy-row"><label>Код аллергена<input name="allergy_code" type="text" required maxlength="40" pattern="[A-Za-z][A-Za-z0-9_]{1,39}" value="${escapeHtml(allergy.code || '')}" placeholder="milk"></label><label>Примечание<input name="allergy_note" type="text" maxlength="500" value="${escapeHtml(allergy.note || '')}" placeholder="Необязательно"></label><button type="button" class="outline-button" data-remove-allergy aria-label="Удалить аллерген">Удалить</button></div>`;
}
function profileMarkup(profile) {
  const selected = (value) => profile.spice_preference === value ? ' selected' : '';
  return `<div class="profile-card"><h2>Ваши настройки</h2><p>Эти параметры сохраняются в профиле API. Роль: ${escapeHtml(state.role === 'admin' ? 'администратор' : state.role === 'user' ? 'пользователь' : 'неизвестна')}.</p>
    <form class="profile-form" id="profile-form">
      <section class="profile-section"><h3>Бюджет и цели на день</h3><div class="profile-grid">
        <label>Обычный бюджет<input name="default_budget_rub" type="number" min="0" max="10000000" step="1" value="${escapeHtml(profile.default_budget_minor == null ? '' : profile.default_budget_minor / 100)}" placeholder="Не задан"></label>
        <label>Валюта<input name="currency" type="text" required maxlength="3" pattern="[A-Za-z]{3}" value="${escapeHtml(profile.currency || 'RUB')}" placeholder="RUB" autocapitalize="characters"></label>
        <label>Калории, ккал<input name="daily_calories_target" type="number" min="1" max="100000" step="1" value="${escapeHtml(profile.daily_calories_target ?? '')}" placeholder="Не задано"></label>
        <label>Белки, г<input name="daily_protein_target_g" type="number" min="0" step="any" value="${escapeHtml(profile.daily_protein_target_g ?? '')}" placeholder="Не задано"></label>
        <label>Жиры, г<input name="daily_fat_target_g" type="number" min="0" step="any" value="${escapeHtml(profile.daily_fat_target_g ?? '')}" placeholder="Не задано"></label>
        <label>Углеводы, г<input name="daily_carbs_target_g" type="number" min="0" step="any" value="${escapeHtml(profile.daily_carbs_target_g ?? '')}" placeholder="Не задано"></label>
      </div><p class="profile-help">Цели на день помогают подбору. Для строгого лимита на одно блюдо укажите калории в форме поиска.</p></section>
      <section class="profile-section"><h3>Вкусы и ограничения</h3><div class="profile-grid">
        <label>Любимые кухни<textarea name="favorite_cuisines" rows="3" placeholder="Грузинская, Итальянская">${escapeHtml((profile.favorite_cuisines || []).join(', '))}</textarea><small>Разделяйте запятыми или переносами строк.</small></label>
        <label>Нелюбимые ингредиенты<textarea name="disliked_ingredients" rows="3" placeholder="кинза, лук">${escapeHtml((profile.disliked_ingredients || []).join(', '))}</textarea><small>Разделяйте запятыми или переносами строк.</small></label>
        <label>Острота<select name="spice_preference"><option value="any"${selected('any')}>Любая</option><option value="mild"${selected('mild')}>Неострое или умеренно острое</option><option value="none"${selected('none')}>Только неострое</option></select></label>
      </div><div class="allergy-heading"><h4>Аллергии</h4><button type="button" class="outline-button" id="add-allergy">+ Добавить</button></div><p class="profile-help">Код аллергена должен совпадать с кодом в API, например milk или peanuts.</p><div id="allergy-list">${(profile.allergies || []).map(allergyRow).join('')}</div></section>
      <section class="profile-section"><h3>Персональная лента</h3><div class="profile-grid"><label>Часовой пояс<input name="timezone" type="text" required maxlength="100" value="${escapeHtml(profile.timezone || 'UTC')}" placeholder="Europe/Moscow" spellcheck="false"></label></div><label class="profile-toggle"><input name="proactive_enabled" type="checkbox" ${profile.proactive_enabled ? 'checked' : ''}><span>Подбирать предложения на день автоматически</span></label><p class="profile-help">Лента рекомендаций пока доступна через API, но ещё не показана на сайте.</p></section>
      <p class="form-error hidden" id="profile-error" role="alert"></p><button class="primary-button" type="submit">Сохранить профиль</button>
    </form><button class="outline-button" id="logout-button">Выйти из аккаунта</button></div>`;
}
async function loadProfile() {
  const container = $('#profile-content');
  if (!state.token) { container.innerHTML = empty('Войдите в аккаунт', 'Настройки профиля доступны после входа.', '<button class="small-button" data-open-auth>Войти</button>'); return; }
  if (state.demo) { container.innerHTML = empty('Backend пока недоступен', 'Проверьте запуск backend, чтобы открыть профиль.'); return; }
  container.innerHTML = '<div class="notice-box">Загружаем профиль…</div>';
  try {
    const profile = await api('/me/profile');
    container.innerHTML = profileMarkup(profile);
  } catch (error) { container.innerHTML = errorBox(error.message); }
}
async function saveProfile(event) {
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector('[type="submit"]');
  const errorElement = $('#profile-error');
  errorElement.classList.add('hidden'); button.disabled = true;
  try {
    const body = profilePatch(new FormData(form));
    await api('/me/profile', { method: 'PATCH', body });
    toast('Профиль обновлён.');
  } catch (error) {
    errorElement.textContent = error.message;
    errorElement.classList.remove('hidden');
  } finally { button.disabled = false; }
}
function openModal(id) { $(`#${id}`).classList.remove('hidden'); setTimeout(() => $(`#${id} input`)?.focus(), 0); }
function closeModal(id) { $(`#${id}`).classList.add('hidden'); }
async function auth(event) {
  event.preventDefault(); const button = $('#auth-submit'); const errorBoxElement = $('#auth-error');
  errorBoxElement.classList.add('hidden'); button.disabled = true;
  try {
    const email = $('#auth-email').value.trim(); const password = $('#auth-password').value;
    const apiKey = $('#auth-api-key').value.trim();
    if (!apiKey) throw new ApiError('Введите командный API-токен.');
    state.apiKey = apiKey;
    const result = await api(`/auth/${state.authMode}`, { method: 'POST', body: { email, password } });
    state.token = result.access_token; state.email = result.user?.email || email; state.role = result.user?.role || '';
    state.feedback = new Map(); syncFeedbackButtons();
    $('#account-label').textContent = state.email.split('@')[0];
    closeModal('auth-modal'); $('#auth-password').value = ''; $('#auth-api-key').value = ''; toast('Вы вошли в аккаунт.');
    await loadRestaurants();
    if (state.token) await loadFeedback();
    if (state.page === 'diary') loadDiary(); if (state.page === 'profile') loadProfile();
  } catch (error) { if (!state.token) state.apiKey = ''; errorBoxElement.textContent = error.message; errorBoxElement.classList.remove('hidden'); }
  finally { button.disabled = false; }
}

document.addEventListener('click', (event) => {
  const target = event.target.closest('button'); if (!target) return;
  if (target.dataset.page) { showPage(target.dataset.page); return; }
  if (target.dataset.restaurant) { openRestaurant(target.dataset.restaurant); return; }
  if (target.dataset.addDiary !== undefined) { addDiary(target.dataset.addDiary); return; }
  if (target.dataset.deleteDiary) { deleteDiary(target.dataset.deleteDiary); return; }
  if (target.dataset.feedbackDish) { rateDish(target.dataset.feedbackDish, target.dataset.feedbackRating, target); return; }
  if (target.id === 'add-allergy') { if (document.querySelectorAll('#allergy-list .allergy-row').length >= 30) { toast('Можно указать не больше 30 аллергий.'); return; } $('#allergy-list').insertAdjacentHTML('beforeend', allergyRow()); $('#allergy-list .allergy-row:last-child input').focus(); return; }
  if (target.dataset.removeAllergy !== undefined) { target.closest('.allergy-row').remove(); return; }
  if (target.dataset.openAuth !== undefined) { openModal('auth-modal'); return; }
  if (target.dataset.close) { closeModal(target.dataset.close); return; }
  if (target.dataset.suggestion) { $('#query-input').value = target.dataset.suggestion; $('#query-input').focus(); return; }
  if (target.dataset.mode) { state.mode = target.dataset.mode; document.querySelectorAll('.mode').forEach((item) => item.classList.toggle('active', item === target)); return; }
  if (target.dataset.authMode) { state.authMode = target.dataset.authMode; document.querySelectorAll('[data-auth-mode]').forEach((item) => item.classList.toggle('active', item === target)); $('#auth-submit').textContent = state.authMode === 'login' ? 'Войти' : 'Зарегистрироваться'; $('#auth-title').textContent = state.authMode === 'login' ? 'Войти в ГастроГид' : 'Регистрация в ГастроГид'; $('#auth-password').minLength = state.authMode === 'login' ? 1 : 8; $('#auth-password').autocomplete = state.authMode === 'login' ? 'current-password' : 'new-password'; return; }
  if (target.id === 'close-menu') { $('#menu-section').classList.add('hidden'); return; }
  if (target.id === 'logout-button') { state.token = ''; state.apiKey = ''; state.email = ''; state.role = ''; state.restaurants = []; state.restaurantTotal = 0; state.demoCount = 0; state.demo = true; state.authRequired = true; state.menuItems = []; state.feedback = new Map(); $('#account-label').textContent = 'Войти'; $('#menu-section').classList.add('hidden'); $('#results-section').classList.add('hidden'); setConnection('auth'); renderRestaurants(); loadProfile(); toast('Вы вышли из аккаунта.'); return; }
  if (target.id === 'clear-results') { $('#results-section').classList.add('hidden'); $('#query-input').focus(); return; }
});
$('#search-form').addEventListener('submit', search);
$('#auth-form').addEventListener('submit', auth);
$('#diary-refresh').addEventListener('click', loadDiary);
$('#diary-date').addEventListener('change', loadDiary);
$('#page-profile').addEventListener('submit', (event) => { if (event.target.id === 'profile-form') saveProfile(event); });
$('#account-button').addEventListener('click', () => state.token ? showPage('profile') : openModal('auth-modal'));
$('#mobile-menu').addEventListener('click', () => $('.sidebar').classList.toggle('open'));
document.querySelectorAll('.modal-backdrop').forEach((modal) => modal.addEventListener('click', (event) => { if (event.target === modal) closeModal(modal.id); }));
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') document.querySelectorAll('.modal-backdrop:not(.hidden)').forEach((modal) => closeModal(modal.id)); });
$('#diary-date').value = today();
showPage(['home', 'restaurants', 'diary', 'profile'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'home');
setConnection('auth');
renderRestaurants();
