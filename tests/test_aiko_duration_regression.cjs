// filename: test_aiko_duration_regression.cjs
// version: 1.0.0
// timestamp: 2026-11-12 15:05:00
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'tools/innermaze_aiko_srt.cjs'),'utf8');
const plugin=fs.readFileSync(path.join(root,'main.js'),'utf8');
const pure=source.split("\nif (typeof module!=='undefined'")[0];
assert(plugin.includes(pure),'Single-file iOS plugin must embed the exact tested Aiko parser');
const parser=vm.runInNewContext(source+'\n;innermazeParseAikoSrt');
const srt=(start,end)=>`1\n00:00:${start} --> 00:00:${end}\nHello\n`;
const near=parser(srt('03,250','05,200'),4);
assert.equal(near[0].start,3.25);
assert.equal(near[0].end,4);
assert.equal(near[0].text,'Hello');
const exact=parser(srt('01,100','04,000'),4);
assert.equal(exact[0].end,4);
assert.throws(()=>parser(srt('03,250','06,100'),4),/caption 1 ends at 6\.100s.*audio duration is 4\.000s.*overrun 2\.100s/);
assert.throws(()=>parser(srt('04,050','04,400'),4),/caption 1 ends at 4\.400s.*starts 4\.050s/);
assert.throws(()=>parser(srt('02,900','01,900'),4),/invalid interval/);
console.log('PASS Aiko duration regression: exact plugin parser, small tail clamp, mismatch diagnostics and invalid intervals');
