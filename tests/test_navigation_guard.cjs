// Reproduce 0.1.8's device bug: opening Clippings from a recording FileView
// reused Innermaze for ALL .md notes and left stale audio on screen.
'use strict';
const assert=require('node:assert/strict');
const Module=require('node:module'), native=Module._load;
class TFile {constructor(path){this.path=path;this.extension='md';this.basename=path.split('/').at(-1).replace(/\.md$/,'');}}
class FileView {constructor(leaf){this.leaf=leaf;this.app=leaf.workspace.app;}}
const notices=[];
Module._load=(name,p,m)=>name==='obsidian'?{
 Plugin:class{},PluginSettingTab:class{},Setting:class{},FileView,Modal:class{},MarkdownView:class{},TFile,
 Notice:class{constructor(m){notices.push(m)}},Platform:{isMobile:false}
}:native(name,p,m);
process.env.INNERMAZE_TEST='1';
const { _internals:{InnermazeView} }=require('../main.js');
const recording=new TFile('Voice Archive/Dream - Chris Wiley sushi restaurant.md');
const article=new TFile('Clippings/The Protests in France.md');
const sources=new Map([[recording.path,'---\nvoice_archive: true\nrecording_id: "abc"\n---\nwords'],[article.path,'# Ordinary article']]);
const workspace={app:null};
workspace.app={vault:{cachedRead:async f=>sources.get(f.path)}};
let redirects=0,resetAudio=0,paused=0;
const leaf={workspace,state:{type:'innermaze-player',state:{file:recording.path}},view:null,
 getViewState(){return this.state;},
 async setViewState(state){redirects++;this.state=state;this.view={file:article};}
};
const plugin={data:{},editingLeaves:new WeakMap()};
const view=new InnermazeView(leaf,plugin);view.file=recording;leaf.view=view;
view.notePath=recording.path;view.currentAudio='Voice Archive/Audio/abc.m4a';
view.audio={pause(){paused++},removeAttribute(name){assert.equal(name,'src');resetAudio++},load(){}};
view.scrollEl={empty(){this.cleared=true}};
view.titleEl={setText(text){this.text=text}};
async function run(){
 // This is the decisive guard: Obsidian must not reuse our custom view when
 // the user navigates to an unrelated markdown note, even though both are .md.
 assert.equal(view.canAcceptExtension('md'),false,
  'Innermaze must not claim all Markdown files; 0.1.8 failed this test');
 // Simulate a restore/session/plugin that bypassed normal navigation routing.
 // The stale content must be removed, then Markdown restored asynchronously.
 view.file=article;leaf.state={type:'innermaze-player',state:{file:article.path}};
 const call=view.onLoadFile(article);
 assert.equal(redirects,0,'must not change leaf type recursively from within FileView.onLoadFile');
 await call;
 assert.equal(view.notePath,'','old recording must be detached');
 assert.equal(view.currentAudio,'','old audio path must be forgotten');
 assert(paused>0&&resetAudio>0,'stale audio must be stopped and source unloaded');
 assert(view.scrollEl.cleared,'stale transcript must be cleared');
 await new Promise(resolve=>setTimeout(resolve,10));
 assert.equal(redirects,1,'restore native Markdown after FileView has returned');
 assert.equal(leaf.state.type,'markdown');
 // If navigation moved AGAIN before the deferred restore, don't hijack it.
 leaf.view=view;view.file=article;leaf.state={type:'innermaze-player',state:{file:article.path}};
 await view.onLoadFile(article);
 leaf.view={file:recording};leaf.state={type:'markdown',state:{file:recording.path}};
 await new Promise(resolve=>setTimeout(resolve,10));
 assert.equal(redirects,1,'stale restore must not overwrite a newer leaf');
 assert.equal(notices.length,0);
 console.log('PASS: ordinary Markdown never commandeered; stale audio reset; deferred recovery and racing navigation');
}
run().catch(e=>{console.error(e);process.exitCode=1});
