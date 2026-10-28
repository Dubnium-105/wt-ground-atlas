import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {gzipSync} from 'node:zlib';
import {createDataClient, probabilityBaseURL, decodeProbability} from '../web/data-client.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');
const source = Buffer.from('{"value":42}');
const descriptor = {url:'shards/sample.bin', gzip_url:'compressed/sample.payload',
  sha256:hash(source), bytes:source.length};
const base = 'https://data.example/atlas/releases/snapshot-a/index.json';

test('external index and optional shard bases preserve project and release paths', () => {
  const external = probabilityBaseURL({url:base}, {});
  assert.equal(new URL(descriptor.url, external).href, 'https://data.example/atlas/releases/snapshot-a/shards/sample.bin');
  const local = probabilityBaseURL({url:'data/probability/index.json'}, {}, 'https://site.example/wt-ground-atlas/app.mjs');
  assert.equal(new URL('shards/a.bin', local).href, 'https://site.example/wt-ground-atlas/data/probability/shards/a.bin');
  const override = probabilityBaseURL({url:base}, {shards_base_url:'../../objects'});
  assert.equal(new URL('shards/a.bin', override).href, 'https://data.example/atlas/objects/shards/a.bin');
});

test('explicit gzip URL is respected; bytes and hashes are checked before decoding', async () => {
  const compressed = gzipSync(source), requests = [];
  const client = createDataClient({fetchImpl:async url => {requests.push(url);return new Response(compressed);}});
  const value = await client.fetchJSON({...descriptor, gzip_bytes:compressed.length, gzip_sha256:hash(compressed)}, base);
  assert.deepEqual(value, {value:42});
  assert.deepEqual(requests, ['https://data.example/atlas/releases/snapshot-a/compressed/sample.payload']);
});

test('already HTTP-decoded gzip body is not decompressed twice, even with hidden encoding headers', async () => {
  const client = createDataClient({fetchImpl:async () => new Response(source)});
  assert.deepEqual(await client.fetchJSON({...descriptor, gzip_bytes:1, gzip_sha256:hash('not the decoded payload')}, base), {value:42});
});

test('gzip network, corrupt payload, and HTTP failures fall back to verified raw bytes', async () => {
  for (const fail of [() => {throw new TypeError('network');}, () => new Response(Buffer.from([0x1f,0x8b,0])), () => new Response('', {status:404})]) {
    const requests = [];
    const client = createDataClient({fetchImpl:async url => {
      requests.push(url); return requests.length === 1 ? fail() : new Response(source);
    }});
    assert.deepEqual(await client.fetchJSON(descriptor, base), {value:42});
    assert.equal(requests.length, 2);
    assert.equal(requests[1], new URL(descriptor.url, base).href);
  }
});

test('raw integrity and byte counts reject corrupted content and a later retry succeeds', async () => {
  let value = source;
  const client = createDataClient({fetchImpl:async () => new Response(value)});
  const raw = {...descriptor, gzip_url:undefined};
  await assert.rejects(client.fetchJSON({...raw, bytes:1}, base), /数据长度不匹配/);
  value = Buffer.from('{"value":43}');
  await assert.rejects(client.fetchJSON(raw, base), /数据校验失败/);
  value = source;
  assert.deepEqual(await client.fetchJSON(raw, base), {value:42});
});

test('concurrent JSON requests deduplicate, including zero-retention mode', async () => {
  let finish, calls = 0;
  const client = createDataClient({maxEntries:0, fetchImpl:() => {
    calls++; return new Promise(resolve => {finish = () => resolve(new Response(source));});
  }});
  const first = client.fetchJSON('https://data.example/a.json');
  const second = client.fetchJSON('https://data.example/a.json');
  assert.equal(first, second); assert.equal(calls, 1);
  finish(); await first;
  const third = client.fetchJSON('https://data.example/a.json');
  assert.equal(calls, 2); finish(); await third;
});

test('LRU evicts by entry count while recently used JSON remains cached', async () => {
  const requests = [];
  const client = createDataClient({maxEntries:2, fetchImpl:async url => {
    requests.push(url); return new Response(source);
  }});
  const load = name => client.fetchJSON(`https://data.example/${name}`);
  await load('a'); await load('b'); await load('a'); await load('c'); await load('a'); await load('b');
  assert.deepEqual(requests.map(url => url.split('/').at(-1)), ['a','b','c','b']);
});

test('byte budget evicts large JSON and does not retain an oversized response', async () => {
  const requests = [];
  const client = createDataClient({maxBytes:source.length, fetchImpl:async url => {
    requests.push(url); return new Response(url.endsWith('large') ? JSON.stringify({text:'x'.repeat(100)}) : source);
  }});
  const load = name => client.fetchJSON(`https://data.example/${name}`);
  await load('a'); await load('b'); await load('a'); await load('large'); await load('large'); await load('a');
  assert.deepEqual(requests.map(url => url.split('/').at(-1)), ['a','b','a','large','large']);
});

test('new checksums at an existing URL cannot reuse old cached JSON', async () => {
  let calls = 0, value = source;
  const client = createDataClient({fetchImpl:async () => {calls++;return new Response(value);}});
  const url = 'https://data.example/mutable.json';
  assert.deepEqual(await client.fetchJSON({url, sha256:hash(value)}), {value:42});
  value = Buffer.from('{"value":43}');
  assert.deepEqual(await client.fetchJSON({url, sha256:hash(value)}), {value:43});
  assert.equal(calls, 2);
});

test('bootstrap no-cache revalidates the manifest and failed JSON parsing is retryable', async () => {
  const requests = []; let value = 'invalid JSON';
  const client = createDataClient({fetchImpl:async (url, options) => {
    requests.push(options.cache); return new Response(value);
  }});
  const manifest = {url:'https://site.example/project/data/manifest.json', cache:'no-cache'};
  await assert.rejects(client.fetchJSON(manifest), SyntaxError);
  value = source;
  await client.fetchJSON(manifest); await client.fetchJSON(manifest);
  assert.deepEqual(requests, ['no-cache','no-cache','no-cache']);
});

test('cancelled heatmap fetch does not start a raw fallback request', async () => {
  const controller = new AbortController(); let calls = 0;
  const client = createDataClient({fetchImpl:async () => {
    calls++; controller.abort(); throw new DOMException('Aborted', 'AbortError');
  }});
  await assert.rejects(client.fetchBytes(descriptor, base, {signal:controller.signal}), {name:'AbortError'});
  assert.equal(calls, 1);
});

test('shared codec preserves finite zero, unknown sentinel, status, and material flags', () => {
  const encoding = {probability_dtype:'uint16-le', quantization_max:65534, unknown_value:65535,
    layout:['probability','status','material_response_flags']};
  const bytes = new ArrayBuffer(12), view = new DataView(bytes);
  [0,32767,65535].forEach((value, index) => view.setUint16(index * 2, value, true));
  new Uint8Array(bytes,6,3).set([3,3,2]); new Uint8Array(bytes,9,3).set([0,1,8]);
  const entry = {width:3,height:1,pixel_pitch_m:0.01,physical_distance_m:400};
  const decoded = decodeProbability(bytes, entry, encoding);
  assert.deepEqual([...decoded.probabilities], [0,.5,NaN]);
  assert.deepEqual([...decoded.status], [3,3,2]);
  assert.deepEqual([...decoded.material_response_flags], [0,1,8]);
  assert.throws(() => decodeProbability(bytes, {...entry,width:-1}, encoding), /网格尺寸/);
  assert.throws(() => decodeProbability(bytes.slice(1), entry, encoding), /尺寸与元数据/);
  assert.throws(() => decodeProbability(bytes, entry, {...encoding,layout:['probability','status']}), /不支持/);
  assert.throws(() => decodeProbability(bytes, entry, {...encoding,quantization_max:0}), /量化参数/);
});
