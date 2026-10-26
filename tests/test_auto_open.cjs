// Regressions for actual Obsidian player-tab and file-open lifecycle.
'use strict';
const assert = require('node:assert/strict');
const Module = require('module');
const originalLoad = Module._load;
class TFile {
  constructor(path) { this.path=path; this.name=path.split('/').at(-1); this.extension=this.name.split('.').at(-1); }
}
class Plugin {}
class PluginSettingTab { constructor(app,plugin){this.app=app;this.plugin=plugin;} }
const notices=[];
const ob={ Plugin, PluginSettingTab, Setting:class{}, ItemView:class{}, Modal:class{},
  Notice:class { constructor(message){notices.push(message);} }, TFile, MarkdownView:class{}, Platform:{isMobile:false} };
Module._load=(name,parent,isMain)=>name==='obsidian'?ob:originalLoad(name,parent,isMain);
process.env.INNERMAZE_TEST='1';
const InnermazePlugin=require('../main.js');
const {isRecordingNote}=InnermazePlugin._internals;
const noteText=id=>`---\nvoice_archive: true\nrecording_id: "${id}"\nvoice_audio: "Voice Archive/Audio/a.m4a"\nvoice_sync: "Voice Archive/Sync/${id}.sync.json"\n---\n\nHello.\n`;
const filename=id=>`Voice Archive/Notes/${id}.md`;
async function scenario(){
  const fileA=new TFile(filename('audio-one'));
  const fileB=new TFile(filename('audio-two'));
  const unrelated=new TFile('Other/Task.md');
  const fake=new TFile(filename('not-a-recording'));
  const moved=new TFile('Other/Moved-Recording.md');
  const notes=new Map([[fileA.path,noteText('audio-one')],[fileB.path,noteText('audio-two')],
    [unrelated.path,'# Tasks\n'],[fake.path,'# Not Innermaze\n'],[moved.path,noteText('moved')]]);
  let fileHandler=null,startup=null,active=fileA,created=0,revealed=0,tabState={type:'empty'};
  const history=[];
  const leaf={view:{},getViewState(){return tabState;},async setViewState(next){
    tabState=next;
    // Real Obsidian sends file-open(null) when a non-Markdown view takes focus.
    active=null;
    fileHandler(null);
    await Promise.resolve();
    this.view={notePath:'',async setRecordingPath(path){this.notePath=path;history.push(path);}};
  }};
  const workspace={
    getActiveFile:()=>active,
    getLeavesOfType:type=>type==='innermaze-player'&&tabState.type===type?[leaf]:[],
    getLeaf:mode=>{assert.equal(mode,'tab');created++;return leaf;},
    getRightLeaf:()=>{throw Error('Do not open sidebar; working player uses tab');},
    async revealLeaf(pane){assert.equal(pane,leaf);revealed++;active=null;fileHandler(null);},
    on(event,callback){assert.equal(event,'file-open');fileHandler=callback;return {};},
    onLayoutReady(cb){startup=cb;},
  };
  const plugin=new InnermazePlugin();const cmds={};
  plugin.app={workspace,
    vault:{cachedRead:async file=>notes.get(file.path),getAbstractFileByPath:path=>[fileA,fileB,unrelated,fake,moved].find(f=>f.path===path)||null,on:()=>({})},
    metadataCache:{getFileCache:file=>({frontmatter:file===moved?{voice_archive:true}:{}})}};
  plugin.loadData=async()=>({positions:{}});
  plugin.saveData=async()=>{};
  plugin.registerView=()=>{};plugin.addSettingTab=()=>{};plugin.registerEvent=()=>{};
  plugin.addCommand=cmd=>{cmds[cmd.id]=cmd.callback;};
  await plugin.onload();
  assert.equal(isRecordingNote(fileA,notes.get(fileA.path)),true);
  assert.equal(isRecordingNote(unrelated,notes.get(unrelated.path)),false);
  assert.equal(isRecordingNote(fake,notes.get(fake.path)),false);
  // Startup must survive the null event emitted DURING player initialization.
  startup();await plugin.autoOpenTask;
  assert.deepEqual(history,[fileA.path]);assert.equal(created,1);assert.equal(revealed,1);
  assert.equal(plugin.lastRecordingPath,fileA.path);
  // Manual Cmd-P command must work while the PLAYER tab, not Markdown, has focus.
  assert.equal(active,null);
  await cmds['open-innermaze-player']();
  assert.deepEqual(history,[fileA.path,fileA.path]);assert.equal(created,1);
  assert.deepEqual(notices,[]);
  // A second recording reuses the tab and does not treat own null event as navigation.
  active=fileB;fileHandler(fileB);await plugin.autoOpenTask;
  assert.equal(history.at(-1),fileB.path);assert.equal(created,1);
  // Ordinary notes and fake archive notes are ignored.
  active=unrelated;fileHandler(unrelated);await plugin.autoOpenTask;
  active=fake;fileHandler(fake);await plugin.autoOpenTask;
  assert.equal(history.length,3);
  // A recording moved outside archive folder still opens using verified frontmatter.
  active=moved;fileHandler(moved);await plugin.autoOpenTask;
  assert.equal(history.at(-1),moved.path);
  // Rapid true navigation: the most recent real recording should win.
  active=fileA;fileHandler(fileA);active=fileB;fileHandler(fileB);await plugin.autoOpenTask;
  assert.equal(history.at(-1),fileB.path);assert.equal(created,1);
  // Mobile uses exactly the same normal tab.
  ob.Platform.isMobile=true;
  active=fileA;fileHandler(fileA);await plugin.autoOpenTask;
  assert.equal(history.at(-1),fileA.path);
  ob.Platform.isMobile=false;
  plugin.data.autoOpenPlayer=false;
  const count=history.length;active=fileB;fileHandler(fileB);await plugin.autoOpenTask;
  assert.equal(history.length,count);
  // Even with auto-open off, explicit command remains functional.
  await cmds['open-innermaze-player']();
  assert.equal(history.at(-1),fileB.path);
  console.log('PASS: real tab lifecycle, null-file events, manual command, reuse, mobile, navigation and off switch');
}
scenario().catch(err=>{console.error(err);process.exitCode=1;});
