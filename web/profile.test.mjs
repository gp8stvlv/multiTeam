import assert from 'node:assert/strict';
import test from 'node:test';
import { profilePatch } from './profile.js';

const base = () => {
  const form = new FormData();
  form.set('currency', 'rub');
  form.set('timezone', 'Europe/Moscow');
  form.set('spice_preference', 'mild');
  return form;
};

test('serializes all profile settings into the API patch', () => {
  const form = base();
  form.set('default_budget_rub', '900');
  form.set('daily_calories_target', '2000');
  form.set('daily_protein_target_g', '100.5');
  form.set('daily_fat_target_g', '70');
  form.set('daily_carbs_target_g', '250');
  form.set('favorite_cuisines', 'Грузинская, Азиатская');
  form.set('disliked_ingredients', 'лук\nкинза');
  form.set('proactive_enabled', 'on');
  form.append('allergy_code', 'MILK');
  form.append('allergy_note', 'Без молока');
  assert.deepEqual(profilePatch(form), {
    timezone: 'Europe/Moscow', currency: 'RUB', default_budget_minor: 90000,
    daily_calories_target: 2000, daily_protein_target_g: 100.5,
    daily_fat_target_g: 70, daily_carbs_target_g: 250,
    favorite_cuisines: ['Грузинская', 'Азиатская'], disliked_ingredients: ['лук', 'кинза'],
    spice_preference: 'mild', allergies: [{ code: 'milk', note: 'Без молока' }], proactive_enabled: true,
  });
});

test('clears optional targets and allergy list explicitly', () => {
  const patch = profilePatch(base());
  assert.equal(patch.default_budget_minor, null);
  assert.equal(patch.daily_calories_target, null);
  assert.deepEqual(patch.allergies, []);
  assert.equal(patch.proactive_enabled, false);
});

test('rejects duplicate allergen codes before sending', () => {
  const form = base();
  form.append('allergy_code', 'milk');
  form.append('allergy_note', '');
  form.append('allergy_code', 'MILK');
  form.append('allergy_note', '');
  assert.throws(() => profilePatch(form), /дважды/);
});
