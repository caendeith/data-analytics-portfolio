/**
 * Скачивание снимка публичного API DefiLlama в data/.
 *
 * Запуск:  node bin/fetch.mjs
 *
 * Зачем отдельный шаг: build.py никогда не ходит в сеть. Он читает только то,
 * что лежит в data/. Поэтому сборку можно перезапускать офлайн и сравнивать
 * дашборд до/после правок на одних и тех же числах.
 *
 * Сознательно не используется ключ: все четыре эндпоинта публичные и без лимитов
 * на разумное число запросов.
 */

import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..');
const DATA = path.join(ROOT, 'data');

const SOURCES = [
  {
    // Дневной ряд суммарной капитализации ВСЕХ стейблкоинов.
    // Важно: без параметра stablecoin= эндпоинт отдаёт сумму по всему рынку,
    // а `?stablecoin=1` — только USDT (174 млрд вместо 282 млрд). Легко перепутать.
    file: 'stablecoins_all.json',
    url: 'https://stablecoins.llama.fi/stablecoincharts/all',
    note: 'дневной ряд капитализации',
  },
  {
    // Список стейблкоинов: нужен, чтобы показать состав рынка и проверить, что ряд не пустой.
    file: 'stablecoins_list.json',
    url: 'https://stablecoins.llama.fi/stablecoins?includePrices=false',
    note: 'состав рынка стейблкоинов',
  },
  {
    // TVL по сетям: одна строка на блокчейн.
    file: 'chains.json',
    url: 'https://api.llama.fi/v2/chains',
    note: 'TVL по сетям',
  },
  {
    // Протоколы с разбивкой по сетям и динамикой за 1/7 дней.
    file: 'protocols.json',
    url: 'https://api.llama.fi/protocols',
    note: 'протоколы',
  },
];

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: { accept: 'application/json', 'user-agent': 'onchain-analysis-portfolio/1.0' },
    signal: AbortSignal.timeout(120_000),
  });
  if (!response.ok) {
    throw new Error(`${url} → HTTP ${response.status}`);
  }
  return response.json();
}

await mkdir(DATA, { recursive: true });

let failed = 0;
for (const source of SOURCES) {
  const started = Date.now();
  try {
    const payload = await fetchJson(source.url);
    const rows = Array.isArray(payload) ? payload.length : Object.keys(payload).length;
    if (!rows) throw new Error('пустой ответ');
    const target = path.join(DATA, source.file);
    await writeFile(target, JSON.stringify(payload), 'utf8');
    const size = (JSON.stringify(payload).length / 1024).toFixed(0);
    console.log(
      `ok   ${source.file.padEnd(22)} ${String(rows).padStart(6)} записей  ${size.padStart(6)} KB  ${Date.now() - started} мс  — ${source.note}`,
    );
  } catch (error) {
    failed += 1;
    console.error(`FAIL ${source.file.padEnd(22)} ${error.message}`);
  }
}

if (failed) {
  console.error(`\nне скачано файлов: ${failed}. build.py без них не запустится.`);
  process.exit(1);
}
console.log('\nснимок в data/ обновлён. Дальше: python build.py');
