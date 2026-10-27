// filename: test_apple_voice_memo_uuid.cjs
// version: 0.1.0
// timestamp: 2026-10-27 10:25:00
'use strict';
const assert = require('node:assert/strict');
const {parseAppleVoiceMemoUuid} = require('../tools/apple_voice_memo_uuid.cjs');
const id = '12345678-1234-4234-8234-1234567890AB';
function u32(num) {const b=Buffer.alloc(4);b.writeUInt32BE(num);return b;}
function atom(kind, ...parts) {const payload=Buffer.concat(parts);return Buffer.concat([u32(payload.length+8),Buffer.from(kind,'latin1'),payload]);}
function mdta(value) {
  const key=Buffer.from('voice-memo-uuid');
  const keys=atom('keys', u32(0), u32(1), u32(key.length+8), Buffer.from('mdta'), key);
  const data=atom('data',u32(1),u32(0),Buffer.from(value));
  const ilst=atom('ilst',atom('\x00\x00\x00\x01',data));
  return atom('meta',u32(0),keys,ilst);
}
function freeform(value) {
  const child = atom('----',
    atom('mean',u32(0),Buffer.from('com.apple.iTunes')),
    atom('name',u32(0),Buffer.from('voice-memo-uuid')),
    atom('data',u32(1),u32(0),Buffer.from(value)));
  return atom('meta',u32(0),atom('ilst',child));
}
const sample = atom('moov', atom('udta',mdta(id.toLowerCase())));
assert.equal(parseAppleVoiceMemoUuid(sample),id);
assert.equal(parseAppleVoiceMemoUuid(new Uint8Array(sample)),id);
assert.equal(parseAppleVoiceMemoUuid(atom('moov',freeform(id))),id);
assert.equal(parseAppleVoiceMemoUuid(atom('moov',atom('udta',atom('free',Buffer.from('hello'))))),null);
assert.equal(parseAppleVoiceMemoUuid(atom('moov',mdta(id),mdta(id))),id);
assert.throws(()=>parseAppleVoiceMemoUuid(atom('moov',mdta(id),mdta('22345678-1234-4234-8234-1234567890AB'))),/Conflicting/);
assert.throws(()=>parseAppleVoiceMemoUuid(atom('moov',mdta('not-a-uuid'))),/Invalid/);
assert.throws(()=>parseAppleVoiceMemoUuid(Buffer.from('bad atom')),/Malformed|Partial/);
console.log('8 embedded UUID parser assertions passed');
