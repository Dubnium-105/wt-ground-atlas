#!/usr/bin/env node
/** Real HTTP/browser smoke using synthetic data, two origins and a site subpath.
 * No production snapshots, client install, GPU or external data service needed.
 */
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {createReadStream} from 'node:fs';
import fs from 'node:fs/promises';
import http from 'node:http';
import {createRequire} from 'node:module';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {promisify} from 'node:util';

const require = createRequire(import.meta.url);
const {chromium} = require('playwright');
const runFile = promisify(execFile);
const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'wt-site-smoke-'));
const disabled = path.join(temporary, 'disabled');
const external = path.join(temporary, 'external');
const dataRequests = [], successfulShards = [], pageErrors = [], allRequests = [];
const mime = {'.html': 'text/html; charset=utf-8', '.css': 'text/css', '.mjs': 'text/javascript',
  '.js': 'text/javascript', '.json': 'application/json', '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2', '.gz': 'application/gzip', '.bin': 'application/octet-stream'};
let failData = false;
let failShards = false;
let missingIndexGzip = false;
let browser;

async function sendFile(response, directory, relative) {
  const filename = path.resolve(directory, relative || 'index.html');
  if (filename !== directory && !filename.startsWith(directory + path.sep)) {
    response.writeHead(403).end();
    return false;
  }
  try {
    const info = await fs.stat(filename);
    if (!info.isFile()) throw new Error('Not a file');
    response.writeHead(200, {'Content-Type': mime[path.extname(filename)] || 'application/octet-stream',
      'Content-Length': info.size, 'Cache-Control': 'no-store'});
    createReadStream(filename).pipe(response);
    return true;
  } catch {
    response.writeHead(404).end();
    return false;
  }
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`));
  });
}

const siteServer = http.createServer((request, response) => {
  const pathname = new URL(request.url, 'http://localhost').pathname;
  for (const [name, directory] of [['disabled', disabled], ['external', external]]) {
    const prefix = `/atlas/${name}/`;
    if (pathname.startsWith(prefix)) {
      void sendFile(response, path.join(directory, 'dist/site'), decodeURIComponent(pathname.slice(prefix.length)));
      return;
    }
  }
  response.writeHead(404).end();
});

const dataServer = http.createServer(async (request, response) => {
  response.setHeader('Access-Control-Allow-Origin', siteOrigin);
  response.setHeader('Vary', 'Origin');
  const pathname = new URL(request.url, 'http://localhost').pathname;
  dataRequests.push(pathname);
  if (failData) {response.writeHead(503, {'Cache-Control': 'no-store'}).end('Synthetic outage'); return;}
  if (missingIndexGzip && pathname.endsWith('/index.json.gz')) {response.writeHead(404).end(); return;}
  if (failShards && pathname.includes('/shards/')) {response.writeHead(503).end('Synthetic shard outage'); return;}
  const prefix = '/releases/fixture/';
  if (!pathname.startsWith(prefix)) {response.writeHead(404).end(); return;}
  const sent = await sendFile(response, path.join(external, 'data/exports/probability'), decodeURIComponent(pathname.slice(prefix.length)));
  if (sent && pathname.includes('/shards/')) successfulShards.push(pathname);
});

let siteOrigin;
try {
  siteOrigin = await listen(siteServer);
  const dataOrigin = await listen(dataServer);
  const python = process.env.GROUNDOPS_PYTHON || 'python';
  for (const [directory, mode] of [[disabled, 'disabled'], [external, 'external']]) {
    const args = ['tests/site_fixture.py', '--output', directory, '--build', '--probability-mode', mode];
    if (mode === 'external') args.push('--probability-base-url', dataOrigin + '/releases/fixture/');
    await runFile(python, args, {cwd: project, env: {...process.env, GROUNDOPS_NODE: process.execPath}});
  }
  browser = await chromium.launch({headless: true,
    executablePath: process.env.GROUNDOPS_CHROMIUM || undefined, args: ['--no-sandbox']});
  const context = await browser.newContext();
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return [siteOrigin, dataOrigin].includes(url.origin) ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => allRequests.push(request.url()));

  await page.goto(`${siteOrigin}/atlas/disabled/#armor`);
  await page.waitForFunction(() => window.__GROUNDOPS__?.ready);
  assert.match(await page.locator('#workspace').innerText(), /暂未提供防护图/);
  assert.equal(dataRequests.length, 0, 'Disabled mode must never contact the data host');
  console.log('PASS disabled probability mode boots directly into armor on a site subpath');

  await page.goto(`${siteOrigin}/atlas/external/`);
  await page.waitForFunction(() => window.__GROUNDOPS__?.ready);
  assert.ok(await page.locator('.lineup-option').count() > 0, 'Real precomputed recommendations render');
  assert.equal(dataRequests.length, 0, 'Startup must not load the probability index or any shard');
  console.log('PASS lightweight startup renders recommendations without probability requests');

  failData = true;
  await page.goto(`${siteOrigin}/atlas/external/#armor`);
  await page.waitForFunction(() => window.__GROUNDOPS__?.ready);
  await page.locator('[data-action="armor-retry"]').waitFor();
  assert.match(await page.locator('#workspace').innerText(), /防护图暂时无法加载/);
  assert.equal(await page.evaluate(() => window.__GROUNDOPS__.ready), true);
  failData = false;
  missingIndexGzip = true;
  failShards = true;
  await page.locator('[data-action="armor-retry"]').click();
  await page.waitForFunction(() => document.querySelector('[data-action="armor-retry"]')?.disabled === false);
  await page.waitForFunction(() => document.querySelector('#workspace')?.textContent.includes('防护图暂时无法加载'));
  assert.ok(dataRequests.some(url => url.endsWith('/index.json')), 'Missing gzip index falls back to JSON');
  assert.ok(dataRequests.some(url => url.includes('/shards/')), 'Index succeeded before the isolated shard outage');
  failShards = false;
  const indexRequestsBeforeRetry = dataRequests.filter(url => /\/index\.json(?:\.gz)?$/.test(url)).length;
  await page.locator('[data-action="armor-retry"]').click();
  await page.waitForFunction(() => !!window.__GROUNDOPS__.getDiagnostics().aim);
  const aim = await page.evaluate(() => window.__GROUNDOPS__.getDiagnostics().aim);
  const mass = aim.known_mass + aim.unknown_mass + aim.no_protected_target_mass + aim.miss_mass;
  assert.ok(Math.abs(mass - 1) < 1e-6, `Probability mass is retained: ${mass}`);
  assert.ok(aim.unknown_mass > 0, 'Unknown pixels remain unknown after loading across origins');
  assert.equal(new Set(successfulShards).size, 1, 'Only the requested angle is downloaded');
  assert.equal(dataRequests.filter(url => /\/index\.json(?:\.gz)?$/.test(url)).length, indexRequestsBeforeRetry,
    'Shard retry reuses the successfully loaded index');
  assert.ok(successfulShards[0].startsWith('/releases/fixture/shards/'));
  console.log('PASS separate-origin gzip index and shard recover after outage and integrate in Worker');

  await page.evaluate(() => {location.hash = '#versions';});
  await page.locator('#change-scope').selectOption('all');
  await page.waitForSelector('.change-row');
  assert.equal(await page.evaluate(() => window.__GROUNDOPS__.getState().view), 'versions');
  assert.deepEqual(pageErrors, []);
  assert.equal(allRequests.filter(url => /\.(?:grp|npz)(?:$|[?#])/.test(url)).length, 0);
  console.log('PASS version workflow stays usable with no raw geometry requests or uncaught browser errors');
} finally {
  if (browser) await browser.close();
  await Promise.all([siteServer, dataServer].map(server => new Promise(resolve => {
    server.close(resolve);
    server.closeAllConnections();
  })));
  await fs.rm(temporary, {recursive: true, force: true});
}
