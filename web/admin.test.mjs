import assert from 'node:assert/strict';
import test from 'node:test';
import { menuSourceKind, reviewedDraftPayload } from './admin.js';

test('menu files use the source kinds expected by the Catalog API', () => {
  assert.equal(menuSourceKind({ name: 'menu.PDF', size: 400 }), 'pdf');
  assert.equal(menuSourceKind({ name: 'menu.html', size: 400 }), 'html');
  assert.equal(menuSourceKind({ name: 'menu.webp', size: 400 }), 'image');
  assert.throws(() => menuSourceKind({ name: 'menu.txt', size: 400 }), /PDF, HTML/);
  assert.throws(() => menuSourceKind({ name: 'menu.pdf', size: 21 * 1024 * 1024 }), /20 МБ/);
});

test('reviewed draft keeps evidence, nutrition and existing dish identity while editing and removing rows', () => {
  const draft = { revision: 4, currency: 'RUB', items: [
    { draft_key: 'a', name: 'Суп', price_minor: null, evidence_ids: ['source-a'], existing_dish_id: 'dish-a', nutrition: { source: 'estimated', method: 'Рецепт', calories_kcal: 200 }, allergens: [{ code: 'milk', status: 'contains', evidence_id: 'source-a' }] },
    { draft_key: 'b', name: 'Лишняя строка', price_minor: 10000, evidence_ids: ['source-b'] },
  ] };
  const fields = new FormData();
  fields.set('include-0', 'on');
  fields.set('name-0', 'Суп с сыром');
  fields.set('description-0', '  Состав из меню  ');
  fields.set('price-0', '450,50');
  fields.set('weight-0', '250');
  const payload = reviewedDraftPayload(draft, fields);
  assert.equal(payload.expected_revision, 4);
  assert.equal(payload.currency, 'RUB');
  assert.equal(payload.items.length, 1);
  assert.equal(payload.items[0].price_minor, 45050);
  assert.equal(payload.items[0].description, 'Состав из меню');
  assert.equal(payload.items[0].portion_weight_g, 250);
  assert.equal(payload.items[0].existing_dish_id, 'dish-a');
  assert.deepEqual(payload.items[0].evidence_ids, ['source-a']);
  assert.equal(payload.items[0].nutrition.method, 'Рецепт');
  assert.equal(payload.items[0].allergens[0].evidence_id, 'source-a');
});

test('review cannot submit an unpriced item or an item without source evidence', () => {
  const fields = new FormData();
  fields.set('include-0', 'on');
  fields.set('name-0', 'Суп');
  fields.set('price-0', '');
  assert.throws(() => reviewedDraftPayload({ revision: 1, currency: 'RUB', items: [{ name: 'Суп', evidence_ids: ['e'] }] }, fields), /цену/);
  fields.set('price-0', '500');
  assert.throws(() => reviewedDraftPayload({ revision: 1, currency: 'RUB', items: [{ name: 'Суп', evidence_ids: [] }] }, fields), /подтверждения/);
});
