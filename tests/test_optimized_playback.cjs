// Innermaze 0.1.6: automatic edit-list playback, tail trim, and hidden fallback.
'use strict';
const { strict: assert } = require('node:assert');
const Module=require('node:module');
const originalLoad=Module._load;
class TFile { constructor(path,size) { this.path=path; this.stat={size}; } }
class ItemView { constructor(leaf) { this.leaf=leaf; } }
Module._load=(name,parent,main)=>name==='obsidian'?{
 Plugin:class{},PluginSettingTab:class{},Setting:class{},ItemView,
 Notice:class{},TFile,Modal:class{},MarkdownView:class{},Platform:{isMobile:true}
}:originalLoad(name,parent,main);
process.env.INNERMAZE_TEST='1';
const { _internals: funcs }=require('../main');
const {InnermazeView,lastTimedSpeechEnd,pauseGapsWithTail,makeSegments,
  createEditListAudio,createPacketCopyAudio,originalToCompiled,compiledToOriginal}=funcs;
const fs=require('node:fs');
const path=require('node:path');
const sample=fs.readFileSync(path.join(__dirname,'fixtures/two-tones20.m4a'));
const fixed=Buffer.from(sample);
const words=[{start:0.5,end:4.4,text:'first'},{start:10.5,end:13.8,text:'last'}];
assert.equal(lastTimedSpeechEnd(words),13.8);
assert.equal(lastTimedSpeechEnd([]),null);
assert.equal(lastTimedSpeechEnd([{start:0,end:99,text:''}]),null);
const inter=[{from:5,to:10}];
const gaps=pauseGapsWithTail(inter,lastTimedSpeechEnd(words),20);
assert.deepEqual(gaps,[{from:5,to:10},{from:14.8,to:20}]);
assert.deepEqual(makeSegments(gaps,20),[{from:0,to:5},{from:10,to:14.8}]);
assert.deepEqual(inter,[{from:5,to:10}],'tail trim cannot mutate original gap detection');
assert.deepEqual(pauseGapsWithTail(inter,18.7,20),inter,'do not trim short tails');
assert.deepEqual(pauseGapsWithTail(inter,null,20),inter,'do not infer untranscribed end');

function newView(audioPath='Voice Archive/Audio/source.m4a') {
 const plugin={data:{skipPauses:true,positions:{}},saveData:async()=>{}};
 let reads=0;
 const f=new TFile(audioPath,sample.length);
 const audio={ paused:true,currentTime:0,duration:20,src:'vault-original',seeking:false,
  pause() {this.paused=true;},load(){this.duration=this.src.startsWith('blob:')?9.8:20;},
  play(){this.paused=false;return Promise.resolve();} };
 const view=new InnermazeView({},plugin);
 view.app={vault:{readBinary:async()=>{reads++;return Uint8Array.from(sample);},
  getResourcePath:()=> 'vault-original'}};
 view.audio=audio;view.audioFile=f;view.currentAudio=f.path;
 view.notePath='Voice Archive/Notes/source.md';view.originalDuration=20;
 view.pauseRanges=[{from:5,to:10}];view.lastSpeechEnd=13.8;
 view.prepStatus={setText(s){view._status=s;}};
 return {view,plugin,audio,reads:()=>reads};
}
function tick(){return new Promise(resolve=>setTimeout(resolve,0));}
async function waitUntil(predicate){for(let i=0;i<50;i++){if(predicate())return;await tick();}throw Error('timeout');}
(async()=>{
 for(const fn of [createEditListAudio,createPacketCopyAudio]) {
  const result=fn(sample,gaps,20);
  assert(Math.abs(result.total-9.8)<0.08, 'end silence must be cut too');
  assert(Math.abs(compiledToOriginal(result.segments,8)-13)<0.05,'highlights follow original time');
  assert(Math.abs(originalToCompiled(result.segments,19)-9.8)<0.06,'seeking into discarded tail clamps at end');
 }
 assert.deepEqual(sample,fixed,'do not alter original AAC payload');
 const {view,plugin,audio,reads}=newView();
 view.enableOptimizedPlayback();
 await waitUntil(()=>view.losslessMode==='edit');
 assert.equal(reads(),1,'prepare automatically once');
 assert(view.losslessUrls.edit.startsWith('blob:'),'native player uses temporary Blob');
 assert.equal(view.losslessResults.edit.total>9.75,true);
 assert.equal(view.losslessResults.packet,undefined,'packet copy is hidden and not built routinely');
 assert.equal(view.toPlaybackTime(13),8);
 assert.equal(view.originalTime(),0);
 plugin.data.skipPauses=false;
 view.selectLosslessMode('original');
 assert.equal(view.losslessMode,'original');
 assert.equal(audio.src,'vault-original');
 plugin.data.skipPauses=true;
 view.enableOptimizedPlayback();
 assert.equal(view.losslessMode,'edit');
 assert.equal(reads(),1,'reuses temporary copy while current recording is open');
 // Realistic iOS decoder-error fallback: edit list was built but rejected on load.
 await view.usePacketFallback('synthetic iOS playback error');
 assert.equal(view.losslessMode,'packet');
 assert.equal(reads(),2);
 assert(view.losslessUrls.packet.startsWith('blob:'));
 assert(view.losslessResults.packet,'packet-copy audio compiled');
 view.losslessResults.packet=null;
 view.selectLosslessMode('original');
 view.enableOptimizedPlayback();
 assert.equal(view.losslessMode,'original','no retry loop when both implementations rejected');
 view.releaseLossless();
 assert.equal(Object.keys(view.losslessUrls).length,0,'Blob URLs released on close/source change');
 assert.equal(Object.keys(view.losslessResults).length,0,'temporary media released');
 // Stale asynchronous vault read must not publish audio after navigation.
 let release;
 const stale=newView();
 stale.view.app.vault.readBinary=()=>new Promise(r=>{release=r;});
 const job=stale.view.prepareOptimizedPlayback(gaps);
 stale.view.releaseLossless();
 release(Uint8Array.from(sample));
 await job;
 assert.equal(stale.view.losslessMode,'original');
 assert.equal(Object.keys(stale.view.losslessResults).length,0);
 // Unsupported source/size: safe original fallback, no plugin/vault changes.
 const other=newView('Voice Archive/Audio/source.mp3');
 await other.view.prepareOptimizedPlayback(gaps);
 assert.equal(other.view.losslessMode,'original');
 assert.equal(other.reads(),0);
 console.log('PASS: trailing-silence removal in both lossless engines');
 console.log('PASS: automatic A, hidden B fallback, skip checkbox, original timeline, Blob cleanup, stale cancellation');
})().catch(e=>{console.error(e);process.exitCode=1;});
