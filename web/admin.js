const MAX_MENU_BYTES = 20 * 1024 * 1024;

export function menuSourceKind(file) {
  if (!file || !file.name) throw new Error('Выберите файл меню.');
  if (!file.size || file.size > MAX_MENU_BYTES) throw new Error('Размер файла должен быть от 1 байта до 20 МБ.');
  const extension = file.name.toLowerCase().split('.').pop();
  if (extension === 'pdf') return 'pdf';
  if (extension === 'html' || extension === 'htm') return 'html';
  if (['png', 'jpg', 'jpeg', 'webp'].includes(extension)) return 'image';
  throw new Error('Поддерживаются PDF, HTML, PNG, JPG и WebP.');
}

function rublesToMinor(value, label) {
  const normalized = String(value).trim().replace(',', '.');
  if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) throw new Error(`Укажите цену в рублях для «${label}».`);
  const [rubles, kopecks = ''] = normalized.split('.');
  const minor = Number(rubles) * 100 + Number(kopecks.padEnd(2, '0'));
  if (!Number.isSafeInteger(minor) || minor > 100000000) throw new Error(`Цена «${label}» слишком велика.`);
  return minor;
}

export function reviewedDraftPayload(draft, fields) {
  if (!draft?.items?.length) throw new Error('В черновике нет блюд. Загрузите меню с читаемым текстом.');
  const items = draft.items.flatMap((item, index) => {
    if (!fields.has(`include-${index}`)) return [];
    const name = String(fields.get(`name-${index}`) ?? '').trim();
    if (!name || name.length > 200) throw new Error(`Проверьте название блюда №${index + 1}.`);
    if (!item.evidence_ids?.length) throw new Error(`Для «${name}» нет подтверждения из загруженного файла.`);
    const rawWeight = String(fields.get(`weight-${index}`) ?? '').trim().replace(',', '.');
    const weight = rawWeight === '' ? null : Number(rawWeight);
    if (weight !== null && (!Number.isFinite(weight) || weight <= 0 || weight > 100000)) throw new Error(`Проверьте вес блюда «${name}».`);
    return [{
      ...item,
      name,
      description: String(fields.get(`description-${index}`) ?? '').trim() || null,
      price_minor: rublesToMinor(fields.get(`price-${index}`) ?? '', name),
      portion_weight_g: weight,
    }];
  });
  if (!items.length) throw new Error('Оставьте в меню хотя бы одно блюдо.');
  return { expected_revision: draft.revision, currency: draft.currency, items };
}
