'use strict';
const assert=require('node:assert/strict');
const Module=require('node:module');
const original=Module._load;
class TFile {constructor(path){this.path=path;this.refresh();}refresh(){this.name=this.path.split('/').at(-1);this.basename=this.name.replace(/\.md$/,'');this.extension=this.name.split('.').at(-1);this.parent={path:this.path.slice(0,this.path.lastIndexOf('/'))};}}
class Plugin {}
class ItemView {constructor(leaf){this.leaf=leaf;}}
class Modal {}
Module._load=(name,parent,isMain)=>name==='obsidian'?{Plugin,ItemView,FileView:ItemView,Modal,PluginSettingTab:class{},Setting:class{},Notice:class{},TFile,MarkdownView:class{},Platform:{isMobile:false}}:original(name,parent,isMain);
process.env.INNERMAZE_TEST='1';
const Innermaze=require('../main.js');
const {validatedRecordingTitle}=Innermaze._internals;
assert.equal(validatedRecordingTitle(' Dream - Chris Wiley sushi restaurant '),'Dream - Chris Wiley sushi restaurant');
for(const s of ['','/path','..','hi:there','bad?name','end.','A'.repeat(200)])assert.throws(()=>validatedRecordingTitle(s));
const originalPath='Voice Archive/Notes/audio-abc.md';
const note=new TFile(originalPath);const fileMap=new Map([[note.path,note]]);
const noteSources=new Map([[note.path,'---\nvoice_archive: true\nrecording_id: "audio-abc"\nsource_sha256: "abc"\nsource_title: "New Recording 59.m4a"\ndisplay_title: "New Recording 59"\nvoice_audio: "Voice Archive/Audio/sha256-abc.m4a"\nvoice_sync: "Voice Archive/Sync/audio-abc.sync.json"\n---\n\nText from dream.\n']]);
const view={notePath:originalPath,titleEl:{setText(value){this.value=value;}}};
const legacy={children:[note]};
const plugin=new Innermaze();
let saved=0, renameEvents=[], renamedTo=[];
plugin.app={
 vault:{
  getAbstractFileByPath(path){return path==='Voice Archive/Notes'?legacy:fileMap.get(path)||null;},
  async cachedRead(file){return noteSources.get(file.path);},
  async read(file){return noteSources.get(file.path);}
 },
 fileManager:{async renameFile(file,target){if(fileMap.has(target))throw Error('collision');const old=file.path;const src=noteSources.get(old);fileMap.delete(old);noteSources.delete(old);file.path=target;file.refresh();fileMap.set(target,file);noteSources.set(target,src);renamedTo.push(target);plugin.relocateRecordingState(old,target);},
  async processFrontMatter(file,fn){const raw=noteSources.get(file.path),fm={display_title:'New Recording 59'};fn(fm);noteSources.set(file.path,raw.replace(/^display_title: .*$/m,`display_title: ${JSON.stringify(fm.display_title)}`));}}
 ,workspace:{getLeavesOfType(){return [{view}];}}
};
plugin.data={positions:{[originalPath]:41}};plugin.lastRecordingPath=originalPath;plugin.saveData=async()=>{saved++;};
async function run(){
 const r=await plugin.migrateLegacyNotes();
 assert.deepEqual(r,{moved:1,skipped:0});
 assert.equal(view.notePath,'Voice Archive/audio-abc.md');
 assert.equal(plugin.data.positions['Voice Archive/audio-abc.md'],41);
 assert(!fileMap.has(originalPath));
 const renamed=await plugin.renameRecording(note.path,'Dream - Chris Wiley sushi restaurant');
 assert.equal(renamed,'Voice Archive/Dream - Chris Wiley sushi restaurant.md');
 assert.equal(view.notePath,renamed);
 assert.equal(view.titleEl.value,'Dream - Chris Wiley sushi restaurant');
 assert.equal(plugin.lastRecordingPath,renamed);
 assert.equal(plugin.data.positions[renamed],41);
 const raw=noteSources.get(renamed);
 assert.match(raw,/display_title: "Dream - Chris Wiley sushi restaurant"/);
 assert.match(raw,/recording_id: "audio-abc"/);
 assert.match(raw,/source_title: "New Recording 59.m4a"/);
 assert.match(raw,/Text from dream/);
 assert.deepEqual(renamedTo,['Voice Archive/audio-abc.md',renamed]);
 const other=new TFile('Voice Archive/Already Here.md');fileMap.set(other.path,other);
 await assert.rejects(()=>plugin.renameRecording(note.path,'Already Here'),/already exists/);
 assert.equal(note.path,renamed);
 console.log('PASS: root migration, rename, player header, original identity, resume, transcript safety, collision refusal');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
