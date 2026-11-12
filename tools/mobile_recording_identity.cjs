// filename: mobile_recording_identity.cjs
// version: 0.2.0
// timestamp: 2026-10-27 14:38:00
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


/** Strict, bounded frontmatter-only reader. Never interpret names as identities. */
function mobileNoteFields(source) {
  const match = String(source).match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!match) return null;
  const fields = Object.create(null);
  const wanted = new Set(['voice_archive', 'recording_id', 'archive_recording_id',
    'source_sha256', 'playback_sha256', 'voice_audio', 'voice_sync']);
  for (const line of match[1].split(/\r?\n/)) {
    const parsed = /^([a-z][a-z0-9_]*):\s*(.*?)\s*$/.exec(line);
    if (!parsed || !wanted.has(parsed[1])) continue;
    const key = parsed[1];
    if (Object.prototype.hasOwnProperty.call(fields, key))
      throw new Error(`Duplicate recording frontmatter key: ${key}`);
    const raw = parsed[2];
    let value;
    if (raw.startsWith('"')) {
      try { value = JSON.parse(raw); }
      catch (_) { throw new Error(`Malformed recording frontmatter: ${key}`); }
      if (typeof value !== 'string') throw new Error(`Non-string recording frontmatter: ${key}`);
    } else if (raw.startsWith("'") && raw.endsWith("'")) {
      value = raw.slice(1, -1).replace(/''/g, "'");
    } else {
      value = raw;
    }
    fields[key] = value;
  }
  if (String(fields.voice_archive || '').toLowerCase() !== 'true') return null;
  if (!fields.recording_id || !fields.voice_audio || !fields.voice_sync)
    throw new Error('Malformed Voice Archive recording requires manual review');
  return fields;
}

function mobileBoundUuid(fields) {
  const ids = [];
  for (const name of ['recording_id', 'archive_recording_id']) {
    const value = fields[name];
    if (!value) continue;
    if (/^vm-/i.test(value)) ids.push(canonical(value.slice(3)));
    else if (name === 'archive_recording_id')
      throw new Error('Unexpected archive_recording_id requires manual review');
  }
  if (new Set(ids).size > 1) throw new Error('Conflicting UUID aliases on recording note');
  return ids[0] || null;
}

const MOBILE_SHA = /^[0-9a-f]{64}$/i;
/** Pure mobile import planner. No writes, filenames, timestamps or AAC guesses.
 * Decision: {action:'create_note'|'reuse_note'|'review_required',reason,notePath?,recordingId}.
 * For an unchanged imported file an identical verified whole-file SHA is sufficient.
 * A same-UUID different-container import MUST await Mango's compressed-packet check.
 */
function planMobileImport(candidate, notes) {
  if (!candidate || !MOBILE_SHA.test(String(candidate.fileSha256 || '')))
    throw new Error('Missing or invalid import SHA-256');
  if (!Array.isArray(notes)) throw new TypeError('Recording note snapshots must be an array');
  const fileSha = candidate.fileSha256.toLowerCase();
  const uid = candidate.appleUuid == null ? null : canonical(candidate.appleUuid);
  const desiredId = uid ? `vm-${uid}` : `audio-${fileSha}`;
  const rows = [];
  const seenPaths = new Set();
  const seenIds = new Set();
  for (const note of notes) {
    if (!note || typeof note.path !== 'string' || typeof note.source !== 'string')
      throw new Error('Invalid recording note snapshot');
    if (seenPaths.has(note.path)) throw new Error('Duplicate recording note path');
    seenPaths.add(note.path);
    const fields = mobileNoteFields(note.source);
    if (!fields) continue;
    const idKey = fields.recording_id.toLowerCase();
    if (seenIds.has(idKey))
      return {action: 'review_required', reason: 'duplicate_recording_id', recordingId: desiredId};
    seenIds.add(idKey);
    for (const key of ['source_sha256', 'playback_sha256']) {
      if (fields[key] && !MOBILE_SHA.test(fields[key]))
        throw new Error(`Invalid ${key} on a recording note`);
    }
    const boundUuid = mobileBoundUuid(fields);
    const exact = [fields.source_sha256, fields.playback_sha256].some(
      digest => digest && digest.toLowerCase() === fileSha);
    rows.push({path: note.path, fields, boundUuid, exact});
  }
  const decision = (action, reason, note) => ({action, reason, recordingId: desiredId,
    ...(note ? {notePath: note.path, existingRecordingId: note.fields.recording_id} : {})});
  if (uid) {
    const bound = rows.filter(row => row.boundUuid === uid);
    if (bound.length > 1) return decision('review_required', 'duplicate_uuid_binding');
    if (bound.length === 1) {
      if (bound[0].exact) return decision('reuse_note', 'same_uuid_exact_file', bound[0]);
      return decision('review_required', 'same_uuid_unverified_audio_version', bound[0]);
    }
    // One immutable file can legitimately be shared by distinct Apple UUIDs.
    // But an anonymous/legacy note matching the file cannot be claimed silently.
    const legacy = rows.filter(row => !row.boundUuid && row.exact);
    if (legacy.length) return decision('review_required', 'possible_legacy_hash_note');
    const collision = rows.find(row => row.fields.recording_id.toLowerCase() === desiredId.toLowerCase());
    if (collision) return decision('review_required', 'recording_id_collision', collision);
    return decision('create_note', 'new_apple_uuid');
  }
  const exact = rows.filter(row => row.exact);
  if (exact.some(row => row.boundUuid)) return decision('review_required', 'missing_uuid_matches_uuid_recording');
  if (exact.length > 1) return decision('review_required', 'missing_uuid_multiple_matches');
  if (exact.length === 1) {
    if (exact[0].fields.recording_id.toLowerCase() === desiredId)
      return decision('reuse_note', 'anonymous_exact_file', exact[0]);
    return decision('review_required', 'missing_uuid_conflicting_identity');
  }
  if (rows.some(row => row.fields.recording_id.toLowerCase() === desiredId))
    return decision('review_required', 'recording_id_collision');
  return decision('create_note', 'new_anonymous_file');
}

module.exports = {parseAppleVoiceMemoUuid, mobileNoteFields, mobileBoundUuid, planMobileImport};
