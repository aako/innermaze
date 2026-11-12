// filename: test_aiko_srt.cjs
// version: 0.1.0
// timestamp: 2026-11-12 14:40:00
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');
const code=fs.readFileSync(require('node:path').join(__dirname,'../tools/innermaze_aiko_srt.cjs'),'utf8');
const ctx=vm.createContext({NOTE_DIR:'Voice Archive'});
const a=vm.runInContext(code+'\n;({innermazeMobileDisplayTitle,innermazeMobileUniqueNotePath,innermazeParseAikoSrt,innermazeAikoSidecar,innermazeAikoUpdateNote})',ctx);
const id='vm-0A25FD21-78E9-4BC1-ABFA-68572D41C59E';
const fakeVault={getFiles:()=>[],getAbstractFileByPath:()=>null};
assert.equal(a.innermazeMobileDisplayTitle('Dream from iPhone.m4a'),'Dream from iPhone');
assert.equal(a.innermazeMobileDisplayTitle('Something.M4A'),'Something');
assert.equal(a.innermazeMobileUniqueNotePath(fakeVault,'Dream from iPhone.m4a',id),'Voice Archive/Dream from iPhone.md');
let existing=['Voice Archive/Dream from iPhone.md'];
let vault={getFiles:()=>existing.map(path=>({path})),getAbstractFileByPath:p=>existing.includes(p)?{path:p}:null};
assert.equal(a.innermazeMobileUniqueNotePath(vault,'Dream from iPhone.m4a',id),
  'Voice Archive/Dream from iPhone (2D41C59E).md');
existing.push('Voice Archive/Dream from iPhone (2D41C59E).md');
assert.equal(a.innermazeMobileUniqueNotePath(vault,'Dream from iPhone.m4a',id),
  'Voice Archive/Dream from iPhone (2D41C59E-2).md');
assert.equal(a.innermazeMobileUniqueNotePath(vault,'Other/name:*?.m4a',id),'Voice Archive/name---.md');
assert.throws(()=>a.innermazeMobileUniqueNotePath(fakeVault,'a.m4a','bogus'),/recording ID/i);
const srt='\uFEFF1\r\n00:00:00,200 --> 00:00:01,900\r\n<i>Hello</i> world\r\n\r\n2\r\n00:00:02,000 --> 00:00:03,150\r\nSecond line\r\nfor this caption\r\n';
const blocks=a.innermazeParseAikoSrt(srt,4);
assert.equal(blocks.length,2);assert.equal(blocks[0].text,'Hello world');
assert.equal(blocks[0].start,0.2);assert.equal(blocks[1].end,3.15);
assert.equal(blocks[1].text,'Second line for this caption');
const sync=a.innermazeAikoSidecar(id,blocks,4,'From Aiko.srt');
assert.equal(sync.format,'voice-archive-sync-v1');assert.equal(sync.source,'aiko');
assert.equal(sync.timing_granularity,'segment');assert.equal(sync.words.length,0);
assert.equal(sync.blocks[0].id,`${id}-00000`);
const original=['---','voice_archive: true',`recording_id: "${id}"`,'source_title: "Dream from iPhone.m4a"',
  'tags:','  - voice-archive','transcription_status: "pending"','---','',''].join('\n');
const updated=a.innermazeAikoUpdateNote(original,blocks);
assert.match(updated,/transcription_source: "aiko"/);assert.match(updated,/transcription_status: "ready"/);
assert.match(updated,/source_title: "Dream from iPhone.m4a"/);
assert.match(updated,/Hello world\n\nSecond line for this caption/);
for(const [bad,duration] of [
  ['1\n00:00:01,000 --> 00:00:04,000\nLate\n',3],
  ['1\n00:00:01,000 --> 00:00:00,800\nBackwards\n',3],
  ['1\n00:00:01,000 --> 00:00:02,000\nOk\n\n2\n00:00:00,300 --> 00:00:01,000\nOut of order',3],
  ['this is not SRT',3],
  ['',3]])assert.throws(()=>a.innermazeParseAikoSrt(bad,duration));
assert.throws(()=>a.innermazeParseAikoSrt(srt,Number.NaN),/duration/);
console.log('PASS Aiko SRT: title/stem, collisions, cue parsing, timestamps, segment-only sidecar, metadata, invalid inputs');
