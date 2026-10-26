// Offline mocked Obsidian workspace tests for auto-open player lifecycle.
'use strict';
const assert = require('node:assert/strict');
const Module = require('module');
const originalLoad = Module._load;
class TFile { constructor(path) { this.path = path; this.name = path.split('/').at(-1); this.extension = this.name.split('.').at(-1); } }
class Plugin {}
class PluginSettingTab { constructor(app, plugin) { this.app=app; this.plugin=plugin; } }
class MarkdownView {}
const obsidian = { Plugin, PluginSettingTab, Setting: class {}, ItemView: class {}, Modal: class {},
  Notice: class {}, TFile, MarkdownView, Platform: { isMobile:false } };
Module._load = (name, parent, isMain) => name === 'obsidian' ? obsidian : originalLoad(name,parent,isMain);
process.env.INNERMAZE_TEST = '1';
const InnermazePlugin = require('../main.js');
const { isRecordingNote } = InnermazePlugin._internals;
const noteText = id => `---\nvoice_archive: true\nrecording_id: "${id}"\nvoice_audio: "Voice Archive/Audio/a.m4a"\nvoice_sync: "Voice Archive/Sync/${id}.sync.json"\n---\n\nHello.\n`;
const filename = id => `Voice Archive/Notes/${id}.md`;

async function scenario() {
  const fileA = new TFile(filename('audio-one'));
  const fileB = new TFile(filename('audio-two'));
  const unrelated = new TFile('Other/Task.md');
  const fakeNote = new TFile(filename('not-an-innermaze-note'));
  const outside = new TFile('Other/Moved-Audio-Note.md');
  const notes = new Map([
    [fileA.path, noteText('audio-one')], [fileB.path, noteText('audio-two')],
    [unrelated.path,'# Tasks\n'], [fakeNote.path,'# Unrelated\n'],
    [outside.path,noteText('moved')]
  ]);
  const pane = {
    pathHistory: [],
    state: { type: 'empty' },
    view: { setRecordingPath: async path => {pane.pathHistory.push(path);} },
    getViewState() {return this.state;},
    async setViewState(state) { this.state=state; }
  };
  let created=0, revealed=0, refocus=0, fileHandler=null, startup=null, active=fileA;
  const editorLeaf={view:{file:fileA}};
  const workspace={
    getActiveFile:()=>active,
    getActiveViewOfType:()=>({leaf:editorLeaf}),
    getLeavesOfType:()=>pane.state.type==='innermaze-player'?[pane]:[],
    getRightLeaf(split){assert.equal(split,true); created++; return pane;},
    getLeaf(){throw Error('desktop must not create a normal tab');},
    async revealLeaf(leaf){assert.equal(leaf,pane);revealed++;},
    setActiveLeaf(leaf,opts){assert.equal(leaf,editorLeaf);assert.equal(opts.focus,false);refocus++;},
    on(event,callback){assert.equal(event,'file-open');fileHandler=callback;return {};},
    onLayoutReady(cb){startup=cb;}
  };
  const metadataCache={getFileCache(file){return {frontmatter: file===outside ? {voice_archive:true}: {}};}};
  const plugin=new InnermazePlugin();
  plugin.app={workspace,metadataCache,vault:{cachedRead:async(file)=>notes.get(file.path), on:()=>({})}};
  plugin.loadData=async()=>({positions:{}});
  plugin.saveData=async()=>{};
  plugin.registerView=()=>{};plugin.addSettingTab=()=>{};plugin.addCommand=()=>{};plugin.registerEvent=()=>{};
  await plugin.onload();
  assert.equal(isRecordingNote(fileA,notes.get(fileA.path)),true);
  assert.equal(isRecordingNote(unrelated,notes.get(unrelated.path)),false);
  assert.equal(isRecordingNote(fakeNote,notes.get(fakeNote.path)),false);
  assert.equal(isRecordingNote(new TFile('Voice Archive/Notes/a.txt'),notes.get(fileA.path)),false);
  // Startup works even when a recording was already selected when plugin loaded.
  startup();await plugin.autoOpenTask;
  assert.equal(created,1);assert.equal(revealed,1);assert.equal(refocus,1);
  assert.deepEqual(pane.pathHistory,[fileA.path]);
  // Reopening the same file reuses player instead of spawning another sidebar pane.
  fileHandler(fileA);await plugin.autoOpenTask;
  assert.equal(created,1); assert.deepEqual(pane.pathHistory,[fileA.path,fileA.path]);
  // A second recording changes playback, keeping the same pane.
  editorLeaf.view.file=fileB;active=fileB;
  fileHandler(fileB);await plugin.autoOpenTask;
  assert.equal(created,1);assert.equal(pane.pathHistory.at(-1),fileB.path);
  // Other Markdown and ordinary notes in the Voice Archive folder are ignored.
  fileHandler(unrelated);await plugin.autoOpenTask;
  fileHandler(fakeNote);await plugin.autoOpenTask;
  assert.equal(pane.pathHistory.length,3);
  // Recognizes a deliberately moved Voice Archive note when metadata cache marks it.
  editorLeaf.view.file=outside;active=outside;
  fileHandler(outside);await plugin.autoOpenTask;
  assert.equal(pane.pathHistory.at(-1),outside.path);
  // Rapid navigation never creates a second player or displays a stale recording.
  fileHandler(fileA);fileHandler(fileB);await plugin.autoOpenTask;
  assert.equal(created,1);assert.equal(pane.pathHistory.at(-1),fileB.path);
  // Mobile uses an ordinary tab; it never attempts to open a desktop sidebar.
  const previousMobile = obsidian.Platform.isMobile;
  obsidian.Platform.isMobile = true;
  const oldGetLeaf = workspace.getLeaf;
  const oldGetRightLeaf = workspace.getRightLeaf;
  const oldGetActiveView = workspace.getActiveViewOfType;
  workspace.getRightLeaf = () => {throw Error('mobile must not create a desktop sidebar');};
  workspace.getLeaf = mode => { assert.equal(mode, 'tab'); return pane; };
  workspace.getActiveViewOfType = () => {throw Error('mobile must not request desktop editor leaf');};
  // Existing pane is reused even when the platform presentation changes.
  fileHandler(fileA); await plugin.autoOpenTask;
  assert.equal(pane.pathHistory.at(-1),fileA.path);
  obsidian.Platform.isMobile = previousMobile;
  workspace.getLeaf = oldGetLeaf;
  workspace.getRightLeaf = oldGetRightLeaf;
  workspace.getActiveViewOfType = oldGetActiveView;
  // Disabled auto-open must not respond to file-open events.
  plugin.data.autoOpenPlayer=false;const length=pane.pathHistory.length;
  fileHandler(fileA);await plugin.autoOpenTask;
  assert.equal(pane.pathHistory.length,length);
  console.log('PASS: recording auto-open, startup, reuse, focus, filters, races and off switch');
}
scenario().catch(err=>{console.error(err);process.exitCode=1;});
