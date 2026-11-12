// filename: test_mobile_recording_identity.cjs
// version: 0.2.0
// timestamp: 2026-10-27 14:38:00
'use strict';
const assert = require('node:assert/strict');
const {parseAppleVoiceMemoUuid: parse, mobileNoteFields, planMobileImport} =
  require('../tools/mobile_recording_identity.cjs');
const A = '0A25FD21-78E9-4BC1-ABFA-68572D41C59E';
const B = '1A25FD21-78E9-4BC1-ABFA-68572D41C59E';
const SHA1 = 'a'.repeat(64), SHA2 = 'b'.repeat(64);
function u32(n) {const x = Buffer.alloc(4); x.writeUInt32BE(n); return x;}
function atom(name, ...parts) {
  const body = Buffer.concat(parts);
  return Buffer.concat([u32(body.length+8),Buffer.from(name,'latin1'),body]);
}
function mdta(value) {
  const key = Buffer.from('voice-memo-uuid');
  const keys = atom('keys',u32(0),u32(1),u32(8+key.length),Buffer.from('mdta'),key);
  const data = atom('data',u32(1),u32(0),Buffer.from(value));
  return atom('meta',u32(0),keys,atom('ilst',atom('\x00\x00\x00\x01',data)));
}
function freeform(value) {
  const entry = atom('----',atom('mean',u32(0),Buffer.from('com.apple.iTunes')),
    atom('name',u32(0),Buffer.from('voice-memo-uuid')),
    atom('data',u32(1),u32(0),Buffer.from(value)));
  return atom('meta',u32(0),atom('ilst',entry));
}
function note(id,sha,{alias=null,path=`Voice Archive/${id}.md`,body='Corrected human text',playback=sha}={}) {
  return {path,source:['---','voice_archive: true',`recording_id: "${id}"`,
    ...(alias?[`archive_recording_id: "${alias}"`]:[]),
    `source_sha256: "${sha}"`,`playback_sha256: "${playback}"`,
    'voice_audio: "Voice Archive/Audio/old.m4a"',
    `voice_sync: "Voice Archive/Sync/${id}.sync.json"`,'---','',body,''].join('\n')};
}
function plan(appleUuid,fileSha256,notes=[]) {return planMobileImport({appleUuid,fileSha256},notes);}
let passed=0;
function test(name,run) {run();passed++;console.log(`ok ${passed} - ${name}`);}
test('mdta UUID, normalized uppercase',()=>assert.equal(parse(atom('moov',atom('udta',mdta(A.toLowerCase())))),A));
test('freeform UUID',()=>assert.equal(parse(atom('moov',freeform(A))),A));
test('no UUID returns null',()=>assert.equal(parse(atom('moov',atom('free',Buffer.from('x')))),null));
test('two contradictory UUIDs stop',()=>assert.throws(()=>parse(atom('moov',mdta(A),mdta(B))),/Conflicting/));
test('bad UUID stops',()=>assert.throws(()=>parse(atom('moov',mdta('bad'))),/Invalid/));
test('truncated MP4 stops',()=>assert.throws(()=>parse(Buffer.from('bad atom')),/Malformed|Partial/));
test('fresh UUID creates vm-ID',()=>assert.deepEqual(plan(A,SHA1),{
  action:'create_note',reason:'new_apple_uuid',recordingId:`vm-${A}`}));
test('same UUID exact bytes reuses renamed note',()=>{
  const n=note(`vm-${A}`,SHA1,{path:'Voice Archive/My new title.md'});
  const p=plan(A,SHA1,[n]);
  assert.equal(p.action,'reuse_note');assert.equal(p.notePath,n.path);
  assert.equal(p.existingRecordingId,`vm-${A}`);
});
test('same UUID remux without packet proof is review',()=>{
  const p=plan(A,SHA2,[note(`vm-${A}`,SHA1)]);
  assert.equal(p.action,'review_required');assert.equal(p.reason,'same_uuid_unverified_audio_version');
});
test('same UUID changed audio is likewise review',()=>{
  assert.equal(plan(A,SHA2,[note(`vm-${A}`,SHA1)]).action,'review_required');
});
test('different UUIDs with identical file bytes stay distinct',()=>{
  assert.equal(plan(B,SHA1,[note(`vm-${A}`,SHA1)]).action,'create_note');
});
test('verified UUID alias preserves existing audio-hash recording_id',()=>{
  const old = note(`audio-${SHA1}`,SHA1,{alias:`vm-${A}`,
    path:'Voice Archive/Renamed by human.md'});
  const p=plan(A,SHA1,[old]);assert.equal(p.action,'reuse_note');
  assert.equal(p.existingRecordingId,`audio-${SHA1}`);
});
test('legacy hash-only note needs review, never silently claimed',()=>{
  const p=plan(A,SHA1,[note(`audio-${SHA1}`,SHA1)]);
  assert.equal(p.action,'review_required');assert.equal(p.reason,'possible_legacy_hash_note');
});
test('two UUID claims are ambiguous',()=>{
  const p=plan(A,SHA1,[note(`vm-${A}`,SHA1),note(`audio-${SHA1}`,SHA1,{alias:`vm-${A}`})]);
  assert.equal(p.action,'review_required');
});
test('missing UUID exact anonymous file reuses one note',()=>{
  assert.equal(plan(null,SHA1,[note(`audio-${SHA1}`,SHA1)]).action,'reuse_note');
});
test('missing UUID matching bound Apple note stops',()=>{
  assert.equal(plan(null,SHA1,[note(`vm-${A}`,SHA1)]).action,'review_required');
});
test('missing UUID does not merge multiple matches',()=>{
  assert.equal(plan(null,SHA1,[note(`audio-${SHA1}`,SHA1),
    note(`other-${SHA1}`,SHA1)]).action,'review_required');
});
test('duplicate recording IDs require review',()=>{
  const p=plan(A,SHA1,[note(`vm-${A}`,SHA1,{path:'Voice Archive/a.md'}),
    note(`vm-${A}`,SHA1,{path:'Voice Archive/b.md'})]);
  assert.equal(p.reason,'duplicate_recording_id');
});
test('mismatching alias on note fails closed',()=>{
  assert.throws(()=>plan(A,SHA1,[note(`vm-${A}`,SHA1,{alias:`vm-${B}`})]),/Conflicting/);
});
test('edited paragraph remains opaque',()=>{
  const n=note(`vm-${A}`,SHA1,{body:'I edited this!'});
  const original=n.source;
  assert.equal(plan(A,SHA1,[n]).action,'reuse_note');assert.equal(n.source,original);
});
test('missing source digest rejected',()=>assert.throws(()=>plan(A,'invalid'),/SHA-256/));
test('frontmatter accurately parses immutable and alias identity',()=>{
  const parsed=mobileNoteFields(note(`audio-${SHA1}`,SHA1,{alias:`vm-${A}`}).source);
  assert.equal(parsed.archive_recording_id,`vm-${A}`);
});
test('stray ordinary markdown ignored',()=>{
  assert.equal(plan(A,SHA1,[{path:'Voice Archive/a.md',source:'---\ntitle: memo\n---\nbody'}]).action,'create_note');
});
console.log(`PASS ${passed} synthetic Apple UUID and mobile reconciliation checks`);
