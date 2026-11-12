// filename: test_aiko_command.cjs
// version: 0.1.0
// timestamp: 2026-11-12 14:40:00
'use strict';
const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
const main=fs.readFileSync(path.join(__dirname,'../main.js'),'utf8');
const mobile=fs.readFileSync(path.join(__dirname,'../tools/mobile_import_methods.cjs'),'utf8');
assert.match(main,/id: 'import-aiko-srt-current-recording'/);
assert.match(main,/name: 'Import Aiko SRT for current recording/);
assert.match(main,/innermazeAikoChooseSrt\(this\)/);
assert.match(main,/innermazeMobileUniqueNotePath\(plugin\.app\.vault, title, plan\.recordingId\)/);
assert.match(mobile,/innermazeMobileUniqueNotePath\(plugin\.app\.vault, title, plan\.recordingId\)/);
assert.match(main,/display_title: \$\{encodeYaml\(innermazeMobileDisplayTitle\(title\)\)\}/);
assert.match(main,/this\.titleEl\.setText\(parsed\.fields\.display_title \|\| note\.basename\)/);
assert.match(main,/timing_granularity:'segment'/);
assert.match(main,/words:\[\]/);
assert.match(main,/BEGIN Agent B mobile UUID import/);
assert.match(main,/BEGIN Agent E desktop-only non-destructive/);
console.log('PASS Aiko command: Obsidian command, player human title, mobile note path, segment-only SRT, mobile+Combine preserved');
