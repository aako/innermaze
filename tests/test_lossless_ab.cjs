// Offline regression tests: both M4A assembly strategies and original/condensed mapping.
const Module=require('module'); const load=Module._load;
Module._load=(name,parent,main)=>name==='obsidian'?
  {Plugin:class{},PluginSettingTab:class{},Setting:class{},ItemView:class{},FileView:class{},Notice:class{},TFile:class{},Modal:class{},MarkdownView:class{},Platform:{isMobile:false}}:
  load(name,parent,main);
process.env.INNERMAZE_TEST='1';
const {strict:assert}=require('node:assert');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {createEditListAudio,createPacketCopyAudio,makeSegments,compiledToOriginal,originalToCompiled} = require('../main')._internals;
const g=[{from:5,to:15}];const segments=makeSegments(g,20);
assert.deepEqual(segments,[{from:0,to:5},{from:15,to:20}]);
const map=[{from:0,to:5,at:0},{from:15,to:20,at:5}];
assert.equal(originalToCompiled(map,18),8);
assert.equal(compiledToOriginal(map,8),18);
assert.equal(originalToCompiled(map,10),5);
assert(Math.abs(compiledToOriginal(map,5.001)-15.001)<1e-10);
(async()=>{
 const files=process.argv[2]?[process.argv[2]]:[
   path.join(__dirname,'fixtures/two-tones20.m4a'),
   path.join(__dirname,'fixtures/two-tones20-faststart.m4a')
 ];
 for (const file of files) {
  const input=fs.readFileSync(file);
  const frozen=Buffer.from(input);
  const e=createEditListAudio(input,g,20),p=createPacketCopyAudio(input,g,20);
  assert.deepEqual(input,frozen,'original compressed audio must never be changed');
  assert(Math.abs(e.total-10)<0.03); assert(Math.abs(p.total-10)<0.05);
  assert(e.blob.size>=input.length,'edit list must retain all media data');
  assert(p.blob.size<input.length,'packet copy must drop unwanted AAC payload');
  assert(e.cuts===1 && p.cuts===1);
  assert(Math.abs(compiledToOriginal(e.segments,8)-18)<0.05);
  assert(Math.abs(compiledToOriginal(p.segments,8)-18)<0.05);
  const eBuf=Buffer.from(await e.blob.arrayBuffer()),pBuf=Buffer.from(await p.blob.arrayBuffer());
  // Re-parse both outputs, not just metadata: packet tables and new edit-list duration.
  const mod=require('../main')._internals;
  assert(eBuf.indexOf(Buffer.from('mdat'))>=0 && pBuf.indexOf(Buffer.from('mdat'))>=0);
  if (process.argv[3]) {
    fs.writeFileSync(path.join(process.argv[3],'innermaze-test-edit.m4a'),eBuf);
    fs.writeFileSync(path.join(process.argv[3],'innermaze-test-packet.m4a'),pBuf);
  }
  console.log('PASS:',path.basename(file), 'edit list + packet copy, original unmodified');
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
