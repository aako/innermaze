// filename: test_combine_ui.cjs
// version: 0.1.0
// timestamp: 2026-11-06 15:37:00
'use strict';
const assert = require('node:assert/strict');
const Module = require('node:module');
const nativeLoad = Module._load;
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const {EventEmitter} = require('node:events');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(),'innermaze-combine-ui-'));
const vault = path.join(tmp,'Cabinet');
const tools = path.join(tmp,'candidate','tools');
fs.mkdirSync(path.join(vault,'.obsidian'),{recursive:true});
fs.mkdirSync(tools,{recursive:true});
const helper = path.join(tools,'innermaze_import_process.py');
const combine = path.join(tools,'innermaze_combine.py');
fs.writeFileSync(helper,'# candidate importer\n');
fs.writeFileSync(combine,'# candidate combiner\n');
let launched = null;
class TFile {constructor(p){this.path=p;this.extension='md';}}
const obsidian = {
  Plugin:class{},PluginSettingTab:class{},Setting:class{},FileView:class{},
  Notice:class{},TFile,Modal:class{},MarkdownView:class{},
  Platform:{isDesktopApp:true}
};
Module._load = function(name,parent,isMain){
  if(name==='obsidian') return obsidian;
  if(name==='child_process' && parent?.filename?.endsWith('/main.js')) return {
    spawn(exe,argv,opts){
      launched = {exe,argv,opts};
      const child = new EventEmitter();
      child.stdout=new EventEmitter();child.stderr=new EventEmitter();
      queueMicrotask(()=>{
        child.stdout.emit('data',Buffer.from(
          'INNERMAZE_COMBINE_JSON={"status":"combined","note":"Voice Archive/combined-xyz.md"}\n'));
        child.emit('close',0);
      });
      return child;
    }
  };
  return nativeLoad(name,parent,isMain);
};
process.env.INNERMAZE_TEST='1';
const Plugin = require('../main');
const {innermazeRunCombine,InnermazeCombineModal} = Plugin._internals;

async function main(){
  assert.equal(typeof innermazeRunCombine,'function');
  assert.equal(typeof InnermazeCombineModal,'function');
  const plugin={data:{importProcessHelperPath:helper},app:{vault:{adapter:{getBasePath:()=>vault}}}};
  const items=[{note:'Voice Archive/One.md',trim_start:'0',trim_end:'0'},
               {note:'Voice Archive/Two.md',trim_start:'0.25',trim_end:'0'}];
  const result=await innermazeRunCombine(plugin,items,'Combined interrupted memo');
  assert.equal(result.status,'combined');
  assert.equal(launched.exe,'/usr/bin/python3');
  assert.equal(launched.opts.shell,false);
  assert.equal(launched.argv[0],fs.realpathSync(combine));
  assert.equal(launched.argv[launched.argv.indexOf('--title')+1],'Combined interrupted memo');
  assert.equal(launched.argv[launched.argv.indexOf('--vault')+1],vault);
  assert.equal(launched.argv.at(-1),'--apply');
  assert.deepEqual(JSON.parse(launched.argv[launched.argv.indexOf('--items-json')+1]),items);
  assert(launched.opts.env.PATH.split(':').includes('/opt/homebrew/bin'));
  assert(!launched.opts.stdio.includes('inherit'));
  fs.unlinkSync(combine);
  await assert.rejects(()=>innermazeRunCombine(plugin,items,'Title'),/unavailable/);
  console.log('PASS desktop Combine: helper from configured worktree, safe JSON argv, ordered items, PATH, fail-closed missing helper');
}
main().catch(error=>{console.error(error);process.exitCode=1;})
  .finally(()=>{fs.rmSync(tmp,{recursive:true,force:true});});
