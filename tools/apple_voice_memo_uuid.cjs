// filename: apple_voice_memo_uuid.cjs
// version: 0.1.0
// timestamp: 2026-10-27 10:25:00
'use strict';

/** Read-only bounded MP4 metadata reader for Agent C's Obsidian/iOS importer.
 * Supports mdta keys/ilst and iTunes ---- freeform metadata; skips mdat.
 * Returns a canonical uppercase UUID or null. No filename or hash inference.
 * UUID alone identifies a *recording*, not an unchanged audio version.
 */
const DECODER = new TextDecoder('utf-8', {fatal: true});
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
function canonical(raw) {
  if (typeof raw !== 'string' || !UUID.test(raw.trim())) throw new Error('Invalid voice-memo-uuid metadata');
  return raw.trim().toUpperCase();
}
function viewOf(input) {
  if (input instanceof ArrayBuffer) return new Uint8Array(input);
  if (ArrayBuffer.isView(input)) return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  throw new TypeError('Expected an ArrayBuffer or typed-array view');
}
function uint(bytes, offset) {
  return new DataView(bytes.buffer, bytes.byteOffset + offset, 4).getUint32(0, false);
}
function text(bytes, start, end) { return DECODER.decode(bytes.subarray(start, end)); }
function boxes(bytes, start, end) {
  const result = [];
  let pos = start;
  while (pos + 8 <= end) {
    let size = uint(bytes, pos);
    const type = String.fromCharCode(...bytes.subarray(pos + 4, pos + 8));
    let header = 8;
    if (size === 1) {
      if (pos + 16 > end) throw new Error('Truncated extended MP4 atom');
      const high = uint(bytes, pos + 8), low = uint(bytes, pos + 12);
      size = high * 0x100000000 + low;
      header = 16;
      if (!Number.isSafeInteger(size)) throw new Error('Oversize MP4 atom');
    } else if (size === 0) size = end - pos;
    if (size < header || size > end - pos) throw new Error('Malformed MP4 atom length');
    result.push({type, start: pos, data: pos + header, end: pos + size});
    pos += size;
  }
  if (pos !== end) throw new Error('Partial MP4 atom');
  return result;
}
function extractData(bytes, item) {
  for (const child of boxes(bytes, item.data, item.end)) {
    if (child.type === 'data' && child.end >= child.data + 8) {
      return text(bytes, child.data + 8, child.end).replace(/\0+$/, '');
    }
  }
  return null;
}
function mdtaKeys(bytes, atom) {
  if (atom.end < atom.data + 8) throw new Error('Malformed MP4 keys atom');
  const count = uint(bytes, atom.data + 4);
  if (count > 50000) throw new Error('Excessive MP4 metadata keys');
  const entries = [];
  let pos = atom.data + 8;
  for (let i = 0; i < count; i++) {
    if (pos + 8 > atom.end) throw new Error('Truncated MP4 metadata key');
    const size = uint(bytes, pos);
    if (size < 8 || pos + size > atom.end) throw new Error('Malformed MP4 metadata key');
    const namespace = text(bytes, pos + 4, pos + 8);
    entries.push(namespace === 'mdta' ? text(bytes, pos + 8, pos + size) : null);
    pos += size;
  }
  return entries;
}
function freeform(bytes, item) {
  let mean = null, name = null, value = null;
  for (const child of boxes(bytes, item.data, item.end)) {
    if ((child.type === 'mean' || child.type === 'name') && child.end >= child.data + 4) {
      const v = text(bytes, child.data + 4, child.end);
      if (child.type === 'mean') mean = v;
      else name = v;
    }
    if (child.type === 'data' && child.end >= child.data + 8) value = text(bytes, child.data + 8, child.end);
  }
  return name === 'voice-memo-uuid' && (mean === 'com.apple.iTunes' || mean === 'mdta' || mean === 'com.apple.VoiceMemos') ? value : null;
}
function readMeta(bytes, meta, found) {
  if (meta.end < meta.data + 4) throw new Error('Truncated meta full box');
  const children = boxes(bytes, meta.data + 4, meta.end);
  const keysBox = children.find(box => box.type === 'keys');
  const keys = keysBox ? mdtaKeys(bytes, keysBox) : [];
  for (const ilst of children.filter(box => box.type === 'ilst')) {
    for (const item of boxes(bytes, ilst.data, ilst.end)) {
      if (item.type === '----') {
        const value = freeform(bytes, item);
        if (value !== null) found.push(canonical(value));
        continue;
      }
      const index = uint(bytes, item.start + 4);
      if (index > 0 && keys[index - 1] === 'voice-memo-uuid') {
        const value = extractData(bytes, item);
        if (value === null) throw new Error('Voice Memo UUID metadata has no data atom');
        found.push(canonical(value));
      }
    }
  }
}
function parseAppleVoiceMemoUuid(input) {
  const bytes = viewOf(input);
  const found = [];
  const visit = (start, end, depth) => {
    if (depth > 6) throw new Error('MP4 metadata nesting too deep');
    for (const atom of boxes(bytes, start, end)) {
      if (atom.type === 'meta') readMeta(bytes, atom, found);
      else if (atom.type === 'moov' || atom.type === 'udta' || atom.type === 'trak') {
        visit(atom.data, atom.end, depth + 1);
      }
    }
  };
  visit(0, bytes.length, 0);
  if (new Set(found).size > 1) throw new Error('Conflicting embedded Apple Voice Memo UUIDs');
  return found[0] || null;
}
module.exports = {parseAppleVoiceMemoUuid};
