/** Static, compressed, content-addressed data transport. No application API required. */
const cache = new Map();

export async function fetchBytes(descriptor, base = import.meta.url) {
  const d = typeof descriptor === 'string' ? {url: descriptor} : descriptor;
  if (!d?.url) throw new Error('缺少静态数据地址');
  let data;
  if (d.gzip_url && typeof DecompressionStream !== 'undefined') {
    const compressed = await fetch(new URL(d.gzip_url, base));
    if (compressed.ok) data = await new Response(compressed.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  }
  if (!data) {
    const response = await fetch(new URL(d.url, base));
    if (!response.ok) throw new Error(`数据加载失败 (${response.status}): ${d.url}`);
    data = await response.arrayBuffer();
  }
  if (d.sha256 && globalThis.crypto?.subtle) {
    const digest = await crypto.subtle.digest('SHA-256', data);
    const actual = [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, '0')).join('');
    if (actual !== d.sha256) throw new Error(`数据校验失败: ${d.url}`);
  }
  return data;
}

export function fetchJSON(descriptor, base = import.meta.url) {
  const key = new URL(typeof descriptor === 'string' ? descriptor : descriptor.url, base).href;
  if (!cache.has(key)) {
    cache.set(key, fetchBytes(descriptor, base).then(data => JSON.parse(new TextDecoder().decode(data))).catch(error => {cache.delete(key); throw error;}));
  }
  return cache.get(key);
}

export function decodeProbability(buffer, entry, encoding) {
  const count = entry.width * entry.height;
  const probabilityByteCount = count * Uint16Array.BYTES_PER_ELEMENT;
  const expected = probabilityByteCount + count * Uint8Array.BYTES_PER_ELEMENT * (encoding.layout.length - 1);
  if (buffer.byteLength !== expected) throw new Error(`概率切片尺寸与元数据不一致: ${buffer.byteLength} / ${expected}`);
  if (encoding.probability_dtype !== 'uint16-le') throw new Error('不支持的概率编码');
  const view = new DataView(buffer), probabilities = new Float64Array(count);
  for (let index = 0; index < count; index++) {
    const q = view.getUint16(index * Uint16Array.BYTES_PER_ELEMENT, true);
    probabilities[index] = q === encoding.unknown_value ? NaN : q / encoding.quantization_max;
  }
  return {
    width: entry.width, height: entry.height, pixel_pitch_m: entry.pixel_pitch_m,
    physical_distance_m: entry.physical_distance_m,
    probabilities,
    status: new Uint8Array(buffer, probabilityByteCount, count).slice(),
    material_response_flags: new Uint8Array(buffer, probabilityByteCount + count, count).slice()
  };
}
