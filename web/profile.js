const optionalNumber = (value, label, { integer = false, positive = false, max = Infinity } = {}) => {
  if (value === '') return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number < (positive ? 1 : 0) || number > max || (integer && !Number.isInteger(number))) {
    throw new Error(`Проверьте поле «${label}».`);
  }
  return number;
};

const list = (value, label, max) => {
  const items = String(value || '').split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
  if (items.length > max || items.some((item) => item.length > 100)) throw new Error(`Проверьте список «${label}».`);
  if (new Set(items.map((item) => item.toLocaleLowerCase('ru-RU'))).size !== items.length) {
    throw new Error(`Уберите повторы из списка «${label}».`);
  }
  return items;
};

export function profilePatch(form) {
  const value = (name) => String(form.get(name) ?? '').trim();
  const currency = value('currency').toUpperCase();
  const timezone = value('timezone');
  const spice = value('spice_preference');
  if (!/^[A-Z]{3}$/.test(currency)) throw new Error('Укажите код валюты из трёх букв, например RUB.');
  if (!timezone) throw new Error('Укажите часовой пояс, например Europe/Moscow.');
  if (!['any', 'mild', 'none'].includes(spice)) throw new Error('Выберите отношение к остроте.');

  const codes = form.getAll('allergy_code');
  const notes = form.getAll('allergy_note');
  if (codes.length !== notes.length || codes.length > 30) throw new Error('Проверьте список аллергий.');
  const allergies = codes.map((rawCode, index) => {
    const code = String(rawCode).trim().toLowerCase();
    const note = String(notes[index]).trim();
    if (!/^[a-z][a-z0-9_]{1,39}$/.test(code) || note.length > 500) {
      throw new Error('Укажите код аллергена латиницей (например milk) и примечание до 500 символов.');
    }
    return { code, note: note || null };
  });
  if (new Set(allergies.map((item) => item.code)).size !== allergies.length) {
    throw new Error('Один и тот же аллерген указан дважды.');
  }

  const budget = optionalNumber(value('default_budget_rub'), 'Обычный бюджет', { integer: true, max: 10000000 });
  return {
    timezone,
    currency,
    default_budget_minor: budget === null ? null : budget * 100,
    daily_calories_target: optionalNumber(value('daily_calories_target'), 'Цель по калориям', { integer: true, positive: true, max: 100000 }),
    daily_protein_target_g: optionalNumber(value('daily_protein_target_g'), 'Цель по белкам'),
    daily_fat_target_g: optionalNumber(value('daily_fat_target_g'), 'Цель по жирам'),
    daily_carbs_target_g: optionalNumber(value('daily_carbs_target_g'), 'Цель по углеводам'),
    favorite_cuisines: list(value('favorite_cuisines'), 'Любимые кухни', 30),
    disliked_ingredients: list(value('disliked_ingredients'), 'Нелюбимые ингредиенты', 100),
    spice_preference: spice,
    allergies,
    proactive_enabled: form.has('proactive_enabled'),
  };
}
