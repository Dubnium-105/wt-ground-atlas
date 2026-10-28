/** Static data transport. Large probability files stay on their data origin and
 * are fetched one view at a time; no application API or full-atlas download. */
export {decodeProbability} from '../shared/probability-codec.mjs';

const descriptorOf = value => typeof value === 'string' ? {url:value} : value;

function resolveDescriptor(value, base) {
  const descriptor = descriptorOf(value);
  if (!descriptor?.url) throw new Error('缺少静态数据地址');
  return {...descriptor, url:new URL(descriptor.url, base).href,
    ...(descriptor.gzip_url ? {gzip_url:new URL(descriptor.gzip_url, base).href} : {})};
}

/** Shard URLs belong to the probability index, not the page or its manifest.
 * An optional shards_base_url is also resolved relative to that index. */
export function probabilityBaseURL(descriptor, index, base = import.meta.url) {
  const resolved = resolveDescriptor(descriptor, base);
  const override = index?.shards_base_url ?? descriptorOf(descriptor)?.shards_base_url;
  if (!override) return new URL(resolved.url);
  const url = new URL(override, resolved.url);
  if (!url.pathname.endsWith('/')) url.pathname += '/';
  return url;
}

async function validateBytes(data, expectedBytes, expectedHash, label) {
  if (expectedBytes !== undefined && data.byteLength !== expectedBytes) {
    throw new Error(`数据长度不匹配: ${label}`);
  }
  if (expectedHash && globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', data);
    const actual = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
    if (actual !== expectedHash.toLowerCase()) throw new Error(`数据校验失败: ${label}`);
  }
  return data;
}

/** Each module/worker gets a bounded LRU of parsed JSON. The byte budget counts
 * uncompressed source bytes, not an exact measurement of the JS object heap.
 * Binary heatmaps are deliberately not retained by this transport. */
export function createDataClient({maxEntries = 32, maxBytes = 16 * 1024 * 1024,
  fetchImpl = (...args) => globalThis.fetch(...args)} = {}) {
  if (!Number.isSafeInteger(maxEntries) || maxEntries < 0 || !Number.isSafeInteger(maxBytes) || maxBytes < 0) {
    throw new TypeError('缓存限制必须是非负整数');
  }
  const cache = new Map(), pending = new Map();
  let retainedBytes = 0;

  async function fetchBytes(value, base = import.meta.url, {signal} = {}) {
    const descriptor = resolveDescriptor(value, base);
    const init = {cache:descriptor.cache ?? 'default', signal};
    let compressedError;
    if (descriptor.gzip_url && typeof DecompressionStream !== 'undefined') {
      try {
        const response = await fetchImpl(descriptor.gzip_url, init);
        if (!response.ok) throw new Error(`数据加载失败 (${response.status}): ${descriptor.gzip_url}`);
        let data = await response.arrayBuffer();
        const prefix = new Uint8Array(data, 0, Math.min(data.byteLength, 2));
        // Fetch may already decode Content-Encoding: gzip. Inspect bytes rather
        // than that header, which cross-origin CORS responses may not expose.
        if (prefix[0] === 0x1f && prefix[1] === 0x8b) {
          await validateBytes(data, descriptor.gzip_bytes, descriptor.gzip_sha256, descriptor.gzip_url);
          data = await new Response(new Blob([data]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
        }
        return await validateBytes(data, descriptor.bytes, descriptor.sha256, descriptor.url);
      } catch (error) {
        if (signal?.aborted || error.name === 'AbortError') throw error;
        compressedError = error;
      }
    }
    try {
      const response = await fetchImpl(descriptor.url, init);
      if (!response.ok) throw new Error(`数据加载失败 (${response.status}): ${descriptor.url}`);
      return await validateBytes(await response.arrayBuffer(), descriptor.bytes, descriptor.sha256, descriptor.url);
    } catch (error) {
      if (compressedError && !signal?.aborted) {
        throw new Error(`压缩与原始数据均未能加载：${error.message}`, {cause:compressedError});
      }
      throw error;
    }
  }

  function fetchJSON(value, base = import.meta.url) {
    const descriptor = resolveDescriptor(value, base);
    // Content identity and length are part of the key: a URL must never allow
    // data cached under an old hash to bypass the new descriptor's checks.
    const key = JSON.stringify([descriptor.url, descriptor.sha256, descriptor.bytes,
      descriptor.gzip_url, descriptor.gzip_sha256, descriptor.gzip_bytes, descriptor.cache]);
    const retain = !['no-cache', 'no-store', 'reload'].includes(descriptor.cache);
    const hit = retain && cache.get(key);
    if (hit) {
      cache.delete(key); cache.set(key, hit);
      return Promise.resolve(hit.value);
    }
    if (pending.has(key)) return pending.get(key);
    const request = fetchBytes(descriptor).then(data => {
      const value = JSON.parse(new TextDecoder().decode(data));
      if (retain && maxEntries && data.byteLength <= maxBytes) {
        while (cache.size && (cache.size >= maxEntries || retainedBytes + data.byteLength > maxBytes)) {
          const oldest = cache.keys().next().value;
          retainedBytes -= cache.get(oldest).bytes; cache.delete(oldest);
        }
        cache.set(key, {value, bytes:data.byteLength}); retainedBytes += data.byteLength;
      }
      return value;
    }).finally(() => pending.delete(key));
    pending.set(key, request);
    return request;
  }

  return {fetchBytes, fetchJSON};
}

const defaultClient = createDataClient();
export const fetchBytes = defaultClient.fetchBytes;
export const fetchJSON = defaultClient.fetchJSON;
