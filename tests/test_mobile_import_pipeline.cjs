// filename: test_mobile_import_pipeline.cjs
// version: 0.2.0
// timestamp: 2026-10-27 14:38:00
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const identity = require('../tools/mobile_recording_identity.cjs');
const methods = fs.readFileSync(path.join(__dirname,'../tools/mobile_import_methods.cjs'),'utf8');
const A='0A25FD21-78E9-4BC1-ABFA-68572D41C59E', B='1A25FD21-78E9-4BC1-ABFA-68572D41C59E';
function u32(n){const x=Buffer.alloc(4);x.writeUInt32BE(n);return x;}
function atom(name,...p){const data=Buffer.concat(p);return Buffer.concat([u32(8+data.length),Buffer.from(name,'latin1'),data]);}
function m4a(uuid,marker='same packets'){
  const key=Buffer.from('voice-memo-uuid');
  const keys=atom('keys',u32(0),u32(1),u32(key.length+8),Buffer.from('mdta'),key);
  const data=atom('data',u32(1),u32(0),Buffer.from(uuid));
  return atom('moov',atom('udta',atom('meta',u32(0),keys,
    atom('ilst',atom('\x00\x00\x00\x01',data)))),atom('mdat',Buffer.from(marker)));
}
class TFile {
  constructor(file,bytes){this.path=file;this.extension=file.split('.').pop();this.parent={path:file.slice(0,file.lastIndexOf('/'))};this.bytes=Buffer.from(bytes);this.stat={size:this.bytes.length};}
}
class Vault {
  constructor(){this.files=new Map();this.directories=new Set();this.audioWrites=0;this.noteWrites=0;}
  getMarkdownFiles(){return [...this.files.values()].filter(f=>f.extension==='md');}
  getFiles(){return [...this.files.values()];}
  getAbstractFileByPath(name){return this.files.get(name)||(this.directories.has(name)?{path:name,folder:true}:null);}
  read(file){return Promise.resolve(file.bytes.toString('utf8'));}
  readBinary(file){return Promise.resolve(file.bytes);}
  createFolder(dir){this.directories.add(dir);return Promise.resolve();}
  create(name,source){if(this.files.has(name))throw Error('Duplicate file');const f=new TFile(name,Buffer.from(source));this.files.set(name,f);this.noteWrites++;return Promise.resolve(f);}
  createBinary(name,buffer){if(this.files.has(name))throw Error('Duplicate file');const f=new TFile(name,buffer);this.files.set(name,f);this.audioWrites++;return Promise.resolve(f);}
}
class ImportProgress {constructor(){this.steps=[];}open(){}close(){}step(n,text){this.steps.push([n,text]);}}
const notices=[];
class Notice {constructor(text){notices.push(text);}}
const vault=new Vault();
const positions={};
const plugin={data:{positions},app:{vault,workspace:{getLeaf:()=>({openFile:async file=>{plugin.lastOpened=file.path;}})}}};
const ctx={innermazeMobileIdentity:identity,TFile,Notice,ImportProgress,
  NOTE_DIR:'Voice Archive',LEGACY_NOTE_DIR:'Voice Archive/Notes',AUDIO_DIR:'Voice Archive/Audio',SYNC_DIR:'Voice Archive/Sync',
  PICKER_MAX_BYTES:200*1024*1024,sha256Buffer:async b=>crypto.createHash('sha256').update(b).digest('hex'),
  isAudioName:n=>n.toLowerCase().endsWith('.m4a'),safeTitle:x=>x,encodeYaml:x=>JSON.stringify(String(x)),
  waitTick:async()=>{},ensureFolder:async(app,dir)=>{for(const part of [dir,dir.split('/')[0]])app.vault.directories.add(part);},
  document:{createElement:()=>{const input={files:[],_callback:null,addEventListener(_,cb){this._callback=cb;},click(){this.pending=this._callback();}};ctx.lastInput=input;return input;}},
};
vm.createContext(ctx);
vm.runInContext(methods,ctx,{filename:'mobile_import_methods.cjs'});
async function send(name,bytes){await ctx.importPickedAudio(plugin);ctx.lastInput.files=[{name,size:bytes.length,arrayBuffer:async()=>bytes}];await ctx.lastInput._callback();}
async function test(){
  console.log('[#####...................] 1/4 | First iPhone import with Apple UUID');
  const original=m4a(A);const digest=crypto.createHash('sha256').update(original).digest('hex');
  await send('Renamed on phone.m4a',original);
  const pathA=`Voice Archive/vm-${A}.md`;
  assert.equal(plugin.lastOpened,pathA);
  assert.equal(vault.noteWrites,1);assert.equal(vault.audioWrites,1);
  assert.equal(identity.mobileNoteFields(await vault.read(vault.files.get(pathA))).recording_id,`vm-${A}`);
  // Human edits and a rename via Obsidian; permanent playback state keyed by note path.
  const current=vault.files.get(pathA);vault.files.delete(pathA);
  const renamed='Voice Archive/Human Title.md';current.path=renamed;current.parent={path:'Voice Archive'};
  current.bytes=Buffer.from(current.bytes.toString()+'\nHuman transcript edit survives.\n');vault.files.set(renamed,current);
  positions[renamed]=129.75;const stable=current.bytes.toString();
  console.log('[##########..............] 2/4 | Reimport preserves note, title, edits, playback state');
  await send('Other phone filename.m4a',original);
  assert.equal(vault.noteWrites,1);assert.equal(vault.audioWrites,1);
  assert.equal(plugin.lastOpened,renamed);assert.equal(vault.files.get(renamed).bytes.toString(),stable);
  assert.equal(positions[renamed],129.75);
  // Import existing vault audio: should reuse exact UUID note and never copy anything.
  await ctx.importVaultAudio(plugin,vault.files.get(`Voice Archive/Audio/sha256-${digest}.m4a`));
  assert.equal(vault.noteWrites,1);assert.equal(vault.audioWrites,1);assert.equal(plugin.lastOpened,renamed);
  console.log('[###############.........] 3/4 | Same UUID remux stops before writing');
  await send('Remuxed.m4a',m4a(A,'same AAC, modified container'));
  assert.equal(vault.noteWrites,1);assert.equal(vault.audioWrites,1);
  assert.match(notices.at(-1),/same_uuid_unverified_audio_version/);
  console.log('[########################] 4/4 | Distinct UUID remains separate');
  await send('Different Apple recording.m4a',m4a(B));
  assert.equal(vault.noteWrites,2);assert.equal(vault.audioWrites,2);
  assert.equal(vault.files.get(renamed).bytes.toString(),stable);
  assert(vault.files.has(`Voice Archive/vm-${B}.md`));
  console.log('PASS iPhone import pipeline: UUID, reimport, renamed note/edit, playback, vault audio, conflicts, distinct UUID');
}
test().catch(error=>{setImmediate(()=>{throw error;});});
