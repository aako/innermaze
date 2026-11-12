// filename: test_aiko_integration.cjs
// version: 0.1.0
// timestamp: 2026-11-12 14:40:00
'use strict';
const assert=require('node:assert/strict');const fs=require('node:fs');
const path=require('node:path');const vm=require('node:vm');
const pure=fs.readFileSync(path.join(__dirname,'../tools/innermaze_aiko_srt.cjs'),'utf8');
const ui=fs.readFileSync(path.join(__dirname,'../tools/innermaze_aiko_ui.cjs'),'utf8');
const id='vm-0A25FD21-78E9-4BC1-ABFA-68572D41C59E';
class TFile{constructor(p,data){this.path=p;this.extension=p.split('.').pop();this.bytes=data;}}
class MockVault{
  constructor(){this.files=new Map();this.modified=[];}
  getAbstractFileByPath(p){return this.files.get(p)||null;}
  read(f){return Promise.resolve(f.bytes);}
  create(p,contents){if(this.files.has(p))throw Error('exists '+p);const f=new TFile(p,contents);this.files.set(p,f);return Promise.resolve(f);}
  modify(f,contents){f.bytes=contents;this.modified.push(f.path);return Promise.resolve();}
  createFolder(){return Promise.resolve();}
  getResourcePath(){return 'app://synthetic/audio.m4a';}
}
class Modal{constructor(){this.contentEl={};}open(){}close(){}}
class Notice{constructor(message){throw Error('unexpected notice '+message);}}
const vault=new MockVault();
const notePath='Voice Archive/Original mobile filename.md';
const original=['---','voice_archive: true',`recording_id: "${id}"`,
  'source_title: "Original mobile filename.m4a"','display_title: "Original mobile filename"',
  'voice_audio: "Voice Archive/Audio/a.m4a"',`voice_sync: "Voice Archive/Sync/${id}.sync.json"`,
  'transcription_status: "pending"','tags:','  - voice-archive','---','',''].join('\n');
const ctx=vm.createContext({NOTE_DIR:'Voice Archive',SYNC_DIR:'Voice Archive/Sync',
  TFile,Modal,Notice,Setting:class{},document:{createElement:name=>{assert.equal(name,'audio');return {
    duration:4, handlers:{},addEventListener(k,cb){this.handlers[k]=cb;},removeEventListener(k){delete this.handlers[k];},removeAttribute(k){this[k]=undefined;},load(){if(this.src){const self=this;queueMicrotask(()=>self.handlers.loadedmetadata?.());}}
  };}},setTimeout,clearTimeout,console,Date,Math,
  isRecordingNote:(note,raw)=>note instanceof TFile&&raw.includes('voice_archive: true'),
  parseNote:raw=>{const f={};for(const line of raw.split('\n')){let m=line.match(/^(recording_id|voice_audio|voice_sync):\s*(.*)$/);if(m)f[m[1]]=JSON.parse(m[2]);}return {fields:f};},
  ensureFolder:async()=>{}});
const funcs=vm.runInContext(pure+'\n'+ui+'\n;({innermazeAikoImportSelected,innermazeAikoCurrentNote})',ctx);
let note=vault.files.set(notePath,new TFile(notePath,original)).get(notePath);
vault.files.set('Voice Archive/Audio/a.m4a',new TFile('Voice Archive/Audio/a.m4a','bytes'));
const plugin={app:{vault,workspace:{activeLeaf:{view:{notePath}},getActiveFile:()=>null}}};
const file={name:'Aiko original.srt',size:84,text:async()=>
  '1\n00:00:00,100 --> 00:00:01,500\nHello.\n\n2\n00:00:01,700 --> 00:00:03,000\nWorld.\n'};
async function test(){
  assert.equal(funcs.innermazeAikoCurrentNote(plugin),note);
  // A pending note with human-written text must also demand a deliberate choice.
  note.bytes=original+'Human-authored draft must survive.\n';
  const humanPending=await funcs.innermazeAikoImportSelected(plugin,note,file,
    {confirmReplace:async()=>false});
  assert.equal(humanPending.status,'cancelled');
  assert.match(note.bytes,/Human-authored draft must survive/);
  assert.equal(vault.files.has(`Voice Archive/Sync/${id}.sync.json`),false);
  note.bytes=original;
  const success=await funcs.innermazeAikoImportSelected(plugin,note,file);
  assert.equal(success.status,'imported');assert.equal(success.captions,2);
  const syncPath=`Voice Archive/Sync/${id}.sync.json`;
  let sync=JSON.parse(vault.files.get(syncPath).bytes);
  assert.equal(sync.recording_id,id);assert.equal(sync.source,'aiko');assert.equal(sync.words.length,0);
  assert.match(note.bytes,/transcription_source: "aiko"/);
  assert.match(note.bytes,/Hello\.\n\nWorld\./);
  const ready=note.bytes,syncReady=vault.files.get(syncPath).bytes;
  const rejected=await funcs.innermazeAikoImportSelected(plugin,note,file,{confirmReplace:async()=>false});
  assert.equal(rejected.status,'cancelled');assert.equal(note.bytes,ready);
  assert.equal(vault.files.get(syncPath).bytes,syncReady);
  const nBefore=vault.modified.length;
  await assert.rejects(()=>funcs.innermazeAikoImportSelected(plugin,note,{name:'wrong.srt',size:10,text:async()=>
    '1\n00:00:01,000 --> 00:00:12,000\nOutside duration'}),/duration/);
  assert.equal(vault.modified.length,nBefore);
  const accepted=await funcs.innermazeAikoImportSelected(plugin,note,file,{confirmReplace:async()=>true});
  assert.equal(accepted.status,'imported');
  const backups=[...vault.files.keys()].filter(k=>k.includes('Transcription Backups'));
  assert.equal(backups.length,2);
  assert(backups.some(k=>vault.files.get(k).bytes===ready));
  assert(backups.some(k=>vault.files.get(k).bytes===syncReady));
  assert.match(note.bytes,/recording_id: "vm-0A25/);
  assert.equal(vault.files.get('Voice Archive/Audio/a.m4a').bytes,'bytes');
  console.log('PASS Aiko integration: active player target, pending import, segment sync, no implicit overwrite, reject invalid duration, explicit backed-up replacement');
}
test().catch(e=>{console.error(e);process.exitCode=1});
