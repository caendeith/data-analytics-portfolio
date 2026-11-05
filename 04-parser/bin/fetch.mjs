#!/usr/bin/env node
/**
 * Браузерный бэкенд загрузки: качает пачку URL через Playwright в кеш.
 *
 * Нужен, когда данные рисуются JS или окружение пускает трафик только через
 * браузер. Имена файлов совпадают с src/fetcher.py::cache_name, поэтому
 * Python-парсер просто читает кеш и в сеть не ходит.
 *
 *   npm i playwright && npx playwright install chromium
 *   node bin/fetch.mjs --cache-dir data/cache --urls-file urls.txt --delay 0.25
 *
 * На stdout печатает одну JSON-строку: {fetched, skipped, failed:[{url,error}]}.
 */
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, existsSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

function parseArgs(argv) {
  const out = { 'cache-dir': 'data/cache', 'urls-file': null, delay: '0.25', concurrency: '4' };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg.startsWith('--')) {
      const key = arg.slice(2);
      const value = argv[i + 1];
      if (value === undefined || value.startsWith('--')) out[key] = 'true';
      else { out[key] = value; i += 1; }
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
if (!args['urls-file']) {
  console.error('нужен --urls-file');
  process.exit(2);
}

// Playwright ищем рядом с проектом, но даём переопределить путь: в некоторых
// окружениях он установлен глобально или лежит в другом node_modules.
const playwrightSpecifier = process.env.PLAYWRIGHT_MODULE || 'playwright';
let chromium;
try {
  ({ chromium } = await import(playwrightSpecifier));
} catch (error) {
  console.error(
    `не удалось загрузить playwright (${playwrightSpecifier}): ${error.message}\n` +
      'установите: npm i playwright && npx playwright install chromium\n' +
      'или укажите путь: PLAYWRIGHT_MODULE=file:///путь/к/node_modules/playwright/index.mjs',
  );
  process.exit(3);
}

const cacheDir = args['cache-dir'];
const delay = Math.max(0, Number(args.delay) || 0);
const concurrency = Math.max(1, Math.min(8, Number(args.concurrency) || 4));

mkdirSync(cacheDir, { recursive: true });

const cacheName = (url) => createHash('sha1').update(url, 'utf8').digest('hex').slice(0, 16) + '.html';

const urls = readFileSync(args['urls-file'], 'utf8')
  .split(/\r?\n/)
  .map((u) => u.trim())
  .filter(Boolean);

const unique = [...new Set(urls)];
const todo = [];
let skipped = 0;
for (const url of unique) {
  const path = join(cacheDir, cacheName(url));
  if (existsSync(path) && statSync(path).size > 0) skipped += 1;
  else todo.push(url);
}

const result = { fetched: 0, skipped, failed: [] };
if (todo.length === 0) {
  console.log(JSON.stringify(result));
  process.exit(0);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  userAgent: 'portfolio-demo-scraper/1.0 (books.toscrape.com sandbox)',
});

let cursor = 0;
let lastStart = 0;

async function worker() {
  const page = await context.newPage();
  for (;;) {
    const index = cursor;
    cursor += 1;
    if (index >= todo.length) break;
    const url = todo[index];

    // глобальная пауза между запросами: вежливость важнее скорости
    const wait = lastStart + delay * 1000 - Date.now();
    if (wait > 0) await sleep(wait);
    lastStart = Date.now();

    let ok = false;
    for (let attempt = 1; attempt <= 3 && !ok; attempt += 1) {
      try {
        const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 25000 });
        if (!response || !response.ok()) throw new Error(`HTTP ${response ? response.status() : 'нет ответа'}`);
        writeFileSync(join(cacheDir, cacheName(url)), await page.content(), 'utf8');
        result.fetched += 1;
        ok = true;
      } catch (error) {
        if (attempt === 3) result.failed.push({ url, error: String(error.message || error).split('\n')[0] });
        else await sleep(1000 * attempt);
      }
    }
  }
  await page.close();
}

await Promise.all(Array.from({ length: concurrency }, () => worker()));
await context.close();
await browser.close();

console.log(JSON.stringify(result));