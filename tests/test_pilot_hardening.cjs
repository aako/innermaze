// filename: test_pilot_hardening.cjs
// version: 0.1.1
// timestamp: 2026-10-27 12:20:00
'use strict';
const assert = require('node:assert/strict');
const Module = require('node:module');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {EventEmitter} = require('node:events');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'innermaze-mango-js-'));
const home = path.join(root, 'Home');
fs.mkdirSync(home);
const vault = path.join(root, 'SyntheticVault');
fs.mkdirSync(path.join(vault, '.obsidian'), {recursive:true});
const helper = path.join(root, 'worktree', 'tools', 'innermaze_import_process.py');
fs.mkdirSync(path.dirname(helper), {recursive:true});
fs.writeFileSync(helper, '#!/usr/bin/env python3\n# synthetic verification fixture\n');
let spawned = null, electronNative = '';
const nativeLoad = Module._load;
class TFile {}
const obsidian = {Plugin:class{},PluginSettingTab:class{},Setting:class{},FileView:class{},Notice:class{},
  TFile,Modal:class{},MarkdownView:class{},Platform:{isDesktopApp:true}};
Module._load = function(name, parent, isMain){
  if(name==='obsidian') return obsidian;
  if(name==='electron') return {webUtils:{getPathForFile:()=>electronNative}};
  if(name==='os' && parent?.filename?.endsWith('/main.js')) return {...os,homedir:()=>home};
  if(name==='child_process' && parent?.filename?.endsWith('/main.js')) return {
    spawn(exe, argv, opts){
      spawned = {exe,argv,opts};
      const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();
      queueMicrotask(()=>{
        child.stdout.emit('data', Buffer.from('INNERMAZE_RESULT_JSON={"status":"imported","note":null}\n'));
        child.emit('close',0);
      });
      return child;
    }
  };
  return nativeLoad(name,parent,isMain);
};
process.env.INNERMAZE_TEST='1';
const Plugin = require('../main');
const {innermazeResolveMangoHelper,innermazeMangoRun,innermazeDesktopFileArgs,
  innermazeResolveDesktopSelection} = Plugin._internals;
const plugin={data:{importProcessHelperPath:''},app:{vault:{adapter:{getBasePath:()=>vault}}}};
async function check(){
  assert.throws(()=>innermazeResolveMangoHelper(plugin),/Set the absolute candidate Mango helper path/);
  plugin.data.importProcessHelperPath = path.join(root,'missing','tools','innermaze_import_process.py');
  assert.throws(()=>innermazeResolveMangoHelper(plugin),/unavailable/);
  plugin.data.importProcessHelperPath = helper;
  assert.equal(innermazeResolveMangoHelper(plugin), fs.realpathSync(helper));
  const opts={transcribe:'now',classify:'after'};
  const native = path.join(root,'Voice Memo Original.m4a');
  fs.writeFileSync(native, 'fake-synthetic-audio');
  electronNative = native;
  const n = await innermazeResolveDesktopSelection({name:'Voice Memo Original.m4a',path:'',size:20});
  assert.equal(n.source,native);assert.equal(n.staged,false);
  assert.deepEqual(innermazeDesktopFileArgs(n.source,'Voice Memo Original.m4a',opts,n.staged),
    ['--file',native,'--source-filename','Voice Memo Original.m4a','--transcribe','now','--classify','after']);
  await innermazeMangoRun(plugin,innermazeDesktopFileArgs(n.source,'Voice Memo Original.m4a',opts,n.staged));
  assert.equal(spawned.exe,'/usr/bin/python3');
  assert.equal(spawned.argv[0],fs.realpathSync(helper));
  assert.equal(spawned.argv.at(-1),'--apply');
  assert.equal(spawned.opts.shell,false);
  // The GUI process may inherit only /usr/bin:/bin: Python must still find ffprobe.
  for (const bin of ['/opt/homebrew/bin', '/usr/local/bin', '/opt/local/bin'])
    assert(spawned.opts.env.PATH.split(':').includes(bin), 'Missing media tool path: ' + bin);
  assert.equal(spawned.opts.env.PATH.endsWith((require('node:process').env.PATH ||
    '/usr/bin:/bin:/usr/sbin:/sbin')),true);
  assert.equal(spawned.argv.includes('--source-filename'),true);
  electronNative = '';
  const payload = Buffer.from('synthetic-staged-audio');
  const selected={name:'A saved voice memo.m4a',size:payload.length,path:'',arrayBuffer:async()=>payload};
  const s=await innermazeResolveDesktopSelection(selected);
  try{
    assert.equal(s.staged,true);assert(fs.existsSync(s.source));
    assert.equal(fs.readFileSync(s.source).toString(),payload.toString());
    const args=innermazeDesktopFileArgs(s.source,selected.name,{transcribe:'later',classify:'skip'},s.staged);
    assert(args.includes('--staged-input'));
    assert(args.includes('A saved voice memo.m4a'));
    await innermazeMangoRun(plugin,args);
    assert.equal(spawned.argv.includes('--staged-input'),true);
    assert.equal(spawned.argv.includes('--transcribe'),true);
    assert.equal(spawned.argv.includes('later'),true);
  }finally{fs.rmSync(s.stagedDir,{recursive:true,force:true});}
  assert.throws(()=>innermazeDesktopFileArgs(native,'../escape.m4a',opts,false),/Invalid original/);
  console.log('PASS: configured worktree helper, launch argv, native file, staged file, original filename, safe argument array');
}
check().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>fs.rmSync(root,{recursive:true,force:true}));
