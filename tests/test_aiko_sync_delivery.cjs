// filename: test_aiko_sync_delivery.cjs
// version: 0.1.0
// timestamp: 2026-11-12 15:37:00
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const ui = fs.readFileSync(path.join(root, 'tools/innermaze_aiko_ui.cjs'), 'utf8');
const parser = fs.readFileSync(path.join(root, 'tools/innermaze_aiko_srt.cjs'), 'utf8');
const source = fs.readFileSync(path.join(root, 'main.js'), 'utf8');
const embedded = ui.split('\nfunction innermazeAikoCurrentNote(')[1];
assert(embedded, 'Aiko UI function missing');
assert(source.includes('function innermazeAikoCurrentNote(' + embedded.split('\nfunction innermazeAikoDuration(')[0]),
  'Single-file plugin Aiko UI diverged from standalone source');
const id = 'vm-0A25FD21-78E9-4BC1-ABFA-68572D41C59E';
const notePath = 'Voice Archive/Original iPhone Memo.md';
const audioPath = 'Voice Archive/Audio/listening.m4a';
const syncPath = `Voice Archive/Sync/${id}.sync.json`;
class TFile {
  constructor(name, bytes) { this.path=name; this.extension=name.split('.').pop(); this.bytes=bytes; }
}
class Vault {
  constructor() {
    this.folders=new Set(['Voice Archive','Voice Archive/Audio']);
    this.files=new Map(); this.writes=[];
  }
  getAbstractFileByPath(name) {
    return this.files.get(name) || (this.folders.has(name) ? {path:name,folder:true} : null);
  }
  read(file) { return Promise.resolve(file.bytes); }
  getResourcePath() {return 'app://test';}
  async createFolder(name) {
    const parent=name.split('/').slice(0,-1).join('/');
    if(parent && !this.folders.has(parent))throw Error('No parent folder: '+name);
    this.folders.add(name);
  }
  async create(name, bytes) {
    const parent=name.split('/').slice(0,-1).join('/');
    if(!this.folders.has(parent))throw Error('Cannot create in missing folder: '+name);
    if(this.files.has(name))throw Error('Already exists: '+name);
    const file = new TFile(name,bytes);this.files.set(name,file);
    this.writes.push({action:'create',name});return file;
  }
  async modify(file,bytes) { file.bytes=bytes;this.writes.push({action:'modify',name:file.path}); }
}
const noteText = ['---','voice_archive: true',`recording_id: "${id}"`,
  'source_title: "Original iPhone Memo.m4a"',
  'display_title: "Original iPhone Memo"',
  `voice_audio: "${audioPath}"`, `voice_sync: "${syncPath}"`,
  'transcription_status: "pending"','---','',''].join('\n');
const vault = new Vault();
const note = new TFile(notePath,noteText);vault.files.set(notePath,note);
vault.files.set(audioPath,new TFile(audioPath,'immutable audio'));
let refreshes=0;
const leaf={view:{notePath,async refreshTranscript() {
  const updated=await vault.read(note);
  const sync=vault.getAbstractFileByPath(syncPath);
  assert.match(updated,/transcription_status: "ready"/);
  assert(sync instanceof TFile,'Player needs a timing sidecar');
  this.display=JSON.parse(await vault.read(sync)).blocks.map(x=>x.text).join(' ');
  refreshes++;
}}};
const workspace={getLeavesOfType:(kind)=>kind==='innermaze-player'?[leaf]:[],
  activeLeaf:leaf,getActiveFile:()=>note};
const plugin={app:{vault,workspace}};
const notice=[];
const ctx=vm.createContext({TFile,NOTE_DIR:'Voice Archive',SYNC_DIR:'Voice Archive/Sync',
  VIEW_TYPE:'innermaze-player',Modal:class{},Setting:class{},
  Notice:class {constructor(text){notice.push(text);}},console,setTimeout,clearTimeout,Date,Math,
  isRecordingNote:(n,raw)=>n instanceof TFile&&raw.includes('voice_archive: true'),
  parseNote:raw=>{
    const fields={};
    for(const line of raw.split('\n')){
      const m=line.match(/^(recording_id|voice_audio|voice_sync):\s*(.*)$/);
      if(m) fields[m[1]]=JSON.parse(m[2]);
    }
    return {fields};
  },
  ensureFolder:async(app,p)=>{
    const pieces=p.split('/'); let current='';
    for(const part of pieces){current=current?current+'/'+part:part;
      if(!app.vault.getAbstractFileByPath(current))await app.vault.createFolder(current);
    }
  },
  document:{createElement:()=>({duration:4,handlers:{},
    addEventListener(k,cb){this.handlers[k]=cb;},removeEventListener(k){delete this.handlers[k];},
    removeAttribute(k){this[k]=undefined;},load(){if(this.src){const self=this;queueMicrotask(()=>self.handlers.loadedmetadata?.());}}
  })}
});
const methods=vm.runInContext(parser+'\n'+ui+'\n;({innermazeAikoImportSelected})',ctx);
const srt={name:'Aiko.srt',size:90,text:async()=>
  '1\n00:00:00,300 --> 00:00:01,200\nPart one\n\n2\n00:00:02,000 --> 00:00:03,700\nPart two\n'};
async function main(){
  console.log('[######..................] 1/4 | Fresh iPhone note lacks Sync folder');
  assert.equal(vault.getAbstractFileByPath('Voice Archive/Sync'),null);
  console.log('[############............] 2/4 | Import Aiko SRT into empty Sync folder');
  const result=await methods.innermazeAikoImportSelected(plugin,note,srt);
  assert.equal(result.status,'imported');
  assert(vault.folders.has('Voice Archive/Sync'));
  assert.equal(JSON.parse(vault.files.get(syncPath).bytes).source,'aiko');
  assert.equal(JSON.parse(vault.files.get(syncPath).bytes).words.length,0);
  assert.match(note.bytes,/transcription_source: "aiko"/);
  console.log('[##################......] 3/4 | Open player reflects imported transcript immediately');
  assert.equal(refreshes,1);
  assert.equal(leaf.view.display,'Part one Part two');
  assert.equal(vault.files.get(audioPath).bytes,'immutable audio');
  assert.match(note.bytes,/recording_id: "vm-0A25FD21/);
  console.log('[########################] 4/4 | Reimport requires explicit consent, never overwrites');
  const before=note.bytes,prev=vault.files.get(syncPath).bytes;
  const cancelled=await methods.innermazeAikoImportSelected(plugin,note,srt,{confirmReplace:async()=>false});
  assert.equal(cancelled.status,'cancelled');
  assert.equal(note.bytes,before);assert.equal(vault.files.get(syncPath).bytes,prev);
  console.log('PASS fresh iPhone note: prepare Sync directory, persist sidecar and note, refresh player, cancel without writes');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
