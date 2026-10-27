// 0.1.8: the recording's own Markdown leaf becomes a FileView, no player tab.
'use strict';
const assert=require('node:assert/strict');
const Module=require('node:module'); const nativeLoad=Module._load;
class TFile {
 constructor(path){this.path=path;this.name=path.split('/').at(-1);this.basename=this.name.replace(/\.md$/,'');this.extension=this.name.split('.').at(-1);}
}
class ItemView {constructor(leaf){this.leaf=leaf;this.app=leaf.workspace.app;}}
class FileView extends ItemView {}
class MarkdownView extends FileView {}
class Plugin {};
const notices=[];
const ob={Plugin,FileView,ItemView,MarkdownView,Modal:class{},Setting:class{},PluginSettingTab:class{},
  Notice:class{constructor(x){notices.push(x)}},TFile,Platform:{isMobile:false}};
Module._load=(name,parent,main)=>name==='obsidian'?ob:nativeLoad(name,parent,main);
process.env.INNERMAZE_TEST='1';
const InnermazePlugin=require('../main');
const {InnermazeView,isRecordingNote}=InnermazePlugin._internals;
function source(id){return `---\nvoice_archive: true\nrecording_id: "${id}"\nvoice_audio: "Voice Archive/Audio/a.m4a"\nvoice_sync: "Voice Archive/Sync/${id}.sync.json"\n---\n\nHello.`}
const noteA=new TFile('Voice Archive/Dream - Chris Wiley sushi restaurant.md');
const noteB=new TFile('Voice Archive/Another Dream.md');
const ordinary=new TFile('Clippings/Article.md');
const other=new TFile('Voice Archive/Not a recording.md');
const outside=new TFile('Elsewhere/Moved recording.md');
const sources=new Map([[noteA.path,source('a')],[noteB.path,source('b')],[ordinary.path,'# Plain article'],[other.path,'# Not a recording'],[outside.path,source('else')]]);
let fileOpen=null, leafChange=null, startup=null, playerFactory=null, createdTabs=0, revealed=0, activeLeaf=null;
const playerPaths=[], changedStates=[], loads=[], leaves=[];
const workspace={
  app:null,activeLeaf:null,
  getActiveFile(){return this.activeLeaf?.view?.file||null;},
  getLeavesOfType(type){return leaves.filter(l=>l.getViewState().type===type);},
  getLeaf(mode){if(mode==='tab'){createdTabs++;throw Error('Plugin must never create a second tab');}throw Error('Unexpected getLeaf '+mode);},
  async revealLeaf(l){assert(leaves.includes(l));revealed++;this.activeLeaf=l;},
  on(event,cb){if(event==='file-open')fileOpen=cb;else if(event==='active-leaf-change')leafChange=cb;else throw Error(event);return{};},
  onLayoutReady(cb){startup=cb;},
};
const app={workspace,
  vault:{cachedRead:async file=>sources.get(file.path),getAbstractFileByPath:path=>[noteA,noteB,ordinary,other,outside].find(f=>f.path===path)||null,on:()=>({})},
  metadataCache:{getFileCache:file=>({frontmatter:{voice_archive:file===outside}})}};
workspace.app=app;
function makeLeaf(file){
 const leaf={workspace,view:{file},isDeferred:false,state:{type:'markdown',state:{file:file.path}},
   getViewState(){return this.state;},
   async setViewState(next){
     changedStates.push({leaf:this,state:next});
     const current=next.state?.file ? app.vault.getAbstractFileByPath(next.state.file):null;
     this.state=next;
     if(next.type==='markdown'){
       this.view={file:current};fileOpen(current);
     }else if(next.type==='innermaze-player'){
       this.view=playerFactory(this);
       this.view.file=current;
       this.view.setRecordingPath=async function(path){this.notePath=path;playerPaths.push({leaf,path});};
       // A FileView owns a real TFile, so switching views can generate a real
       // file-open event. It must not cancel its own initialization.
       fileOpen(current);
       await this.view.onLoadFile(current);
     }else throw Error('Unexpected type');
   },
   async loadIfDeferred(){loads.push(this);this.isDeferred=false;this.view=playerFactory(this);this.view.file=app.vault.getAbstractFileByPath(this.state.state.file);this.view.setRecordingPath=async function(path){this.notePath=path;playerPaths.push({leaf,path});};await this.view.onLoadFile(this.view.file);}
 };leaves.push(leaf);return leaf;
}
function navigate(leaf,file){leaf.state={type:'markdown',state:{file:file.path}};leaf.view={file};workspace.activeLeaf=leaf;fileOpen(file);}
async function scenario(){
 const plugin=new InnermazePlugin();plugin.app=app;
 const commands={};plugin.loadData=async()=>({positions:{}});plugin.saveData=async()=>{};
 plugin.registerView=(type,fn)=>{assert.equal(type,'innermaze-player');playerFactory=fn;};
 plugin.addSettingTab=()=>{};plugin.registerEvent=()=>{};
 plugin.addCommand=x=>{commands[x.id]=x.callback;};
 await plugin.onload();
 assert(isRecordingNote(noteA,sources.get(noteA.path)));
 const l1=makeLeaf(noteA);workspace.activeLeaf=l1;
 const oldTab={state:{type:'innermaze-player',state:{}},view:{},detached:false,
   getViewState(){return this.state;},detach(){this.detached=true;leaves.splice(leaves.indexOf(this),1);}};
 leaves.push(oldTab);
 startup();await plugin.autoOpenTask;
 assert(oldTab.detached,'legacy fileless utility tab is cleaned up on startup');
 assert.equal(createdTabs,0);assert.equal(l1.state.type,'innermaze-player');
 assert.equal(l1.state.state.file,noteA.path);
 assert(l1.view instanceof FileView);assert(l1.view instanceof InnermazeView);
 assert.equal(l1.view.getDisplayText(),noteA.basename);
 assert.equal(workspace.getActiveFile(),noteA);
 assert.deepEqual(playerPaths.map(x=>x.path),[noteA.path]);
 const version=plugin.autoOpenRequest;
 fileOpen(noteA);await plugin.autoOpenTask;assert.equal(plugin.autoOpenRequest,version,'FileView event must not recursively open');
 // Edit should stay in SAME leaf, and keep native Markdown visible despite file-open.
 await l1.view.editTranscript();await plugin.autoOpenTask;
 assert.equal(l1.state.type,'markdown');assert.equal(l1.state.state.file,noteA.path);assert.equal(createdTabs,0);
 fileOpen(noteA);await plugin.autoOpenTask;assert.equal(l1.state.type,'markdown','editing remains possible');
 // Explicit command toggles the same file into player mode.
 await commands['open-innermaze-player']();
 assert.equal(l1.state.type,'innermaze-player');assert.equal(createdTabs,0);
 // Opening another note navigates in the same leaf; editing suppression expires.
 navigate(l1,noteB);await plugin.autoOpenTask;assert.equal(l1.view.notePath,noteB.path);
 // Different tabs keep DIFFERENT recording contexts. No global player leaf reuse.
 const l2=makeLeaf(noteA);workspace.activeLeaf=l2;fileOpen(noteA);await plugin.autoOpenTask;
 assert.equal(l2.view.notePath,noteA.path);assert.equal(l1.view.notePath,noteB.path);
 // Ordinary markdown never becomes player.
 navigate(l2,ordinary);await plugin.autoOpenTask;assert.equal(l2.state.type,'markdown');
 navigate(l2,other);await plugin.autoOpenTask;assert.equal(l2.state.type,'markdown');
 navigate(l2,outside);await plugin.autoOpenTask;assert.equal(l2.view.notePath,outside.path);
 // Direct/restored custom view erroneously handed an ordinary note:
 // the fallback must run after the FileView state setter, not inside it.
 l2.view.file=ordinary; l2.state={type:'innermaze-player',state:{file:ordinary.path}};
 await l2.view.onLoadFile(ordinary);
 await new Promise(resolve=>setTimeout(resolve,10));await plugin.autoOpenTask;
 assert.equal(l2.state.type,'markdown');assert.equal(l2.state.state.file,ordinary.path);
 // Mobile goes through identical path.
 ob.Platform.isMobile=true;navigate(l2,noteA);await plugin.autoOpenTask;
 assert.equal(l2.view.notePath,noteA.path);ob.Platform.isMobile=false;
 // Restored DeferredView gets hydrated without a new tab.
 l2.view={file:noteA};l2.isDeferred=true;
 await commands['open-innermaze-player']();
 assert.equal(loads.length,1);assert.equal(l2.view.notePath,noteA.path);
 assert.equal(createdTabs,0);
 // Navigation ordering: file-open may fire BEFORE active leaf's file changes.
 // The delayed auto-open callback must see the newly committed file and open
 // its player, instead of silently dropping the event as in 0.1.8.
 l2.view={file:noteB};l2.state={type:'markdown',state:{file:noteB.path}};
 workspace.activeLeaf=l2;
 fileOpen(noteA); // current leaf is still noteB here
 setTimeout(()=>{l2.view={file:noteA};l2.state={type:'markdown',state:{file:noteA.path}};},0);
 await plugin.autoOpenTask;
 assert.equal(l2.state.type,'innermaze-player');
 assert.equal(l2.view.notePath,noteA.path);
 // Other file navigation paths can change active leaf without file-open.
 l2.view={file:noteB};l2.state={type:'markdown',state:{file:noteB.path}};
 workspace.activeLeaf=l2;
 leafChange(l2);await plugin.autoOpenTask;
 assert.equal(l2.state.type,'innermaze-player');
 assert.equal(l2.view.notePath,noteB.path);
 assert.equal(createdTabs,0);
 assert.equal(notices.length,0);
 console.log('PASS: same file tab, file title and active file, native editor toggle, manual switch, mobile, multiple tabs, ordinary Markdown and DeferredView');
}
scenario().catch(e=>{console.error(e);process.exitCode=1;});
