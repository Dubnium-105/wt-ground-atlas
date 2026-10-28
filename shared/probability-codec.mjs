/** Pure probability decoder shared by offline integration and the browser.
 * Keep numerical semantics independent of networking and deployment changes. */
export function decodeProbability(buffer, entry, encoding) {
  const width = entry?.width, height = entry?.height;
  if (!Number.isSafeInteger(width) || width <= 0 || !Number.isSafeInteger(height) || height <= 0 ||
      !Number.isSafeInteger(width * height)) throw new Error('概率切片网格尺寸无效');
  if (encoding?.probability_dtype !== 'uint16-le' ||
      JSON.stringify(encoding.layout) !== JSON.stringify(['probability', 'status', 'material_response_flags'])) {
    throw new Error('不支持的概率编码');
  }
  if (!Number.isInteger(encoding.quantization_max) || encoding.quantization_max <= 0 || encoding.quantization_max > 65534 ||
      !Number.isInteger(encoding.unknown_value) || encoding.unknown_value <= encoding.quantization_max || encoding.unknown_value > 65535) {
    throw new Error('概率量化参数无效');
  }
  const count = width * height;
  const probabilityByteCount = count * Uint16Array.BYTES_PER_ELEMENT;
  const expected = probabilityByteCount + count * Uint8Array.BYTES_PER_ELEMENT * 2;
  if (buffer.byteLength !== expected) throw new Error(`概率切片尺寸与元数据不一致: ${buffer.byteLength} / ${expected}`);
  const view = new DataView(buffer), probabilities = new Float64Array(count);
  for (let index = 0; index < count; index++) {
    const q = view.getUint16(index * Uint16Array.BYTES_PER_ELEMENT, true);
    if (q > encoding.quantization_max && q !== encoding.unknown_value) throw new Error('概率量化值超出范围');
    probabilities[index] = q === encoding.unknown_value ? NaN : q / encoding.quantization_max;
  }
  return {
    width, height, pixel_pitch_m:entry.pixel_pitch_m,
    physical_distance_m:entry.physical_distance_m,
    probabilities,
    status:new Uint8Array(buffer, probabilityByteCount, count).slice(),
    material_response_flags:new Uint8Array(buffer, probabilityByteCount + count, count).slice()
  };
}
