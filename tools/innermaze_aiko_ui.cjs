// filename: innermaze_aiko_ui.cjs
// version: 0.1.0
// timestamp: 2026-11-12 14:40:00
// Inserted into main.js. No Node imports; runs on iPhone and desktop Obsidian.
function innermazeAikoCurrentNote(plugin) {
  const leaf=plugin.app.workspace.activeLeaf;
  const playerPath=leaf?.view?.notePath;
  const active=plugin.app.workspace.getActiveFile();
  const candidate=playerPath ? plugin.app.vault.getAbstractFileByPath(playerPath) : active;
  if (!(candidate instanceof TFile) || candidate.extension!=='md')
    throw new Error('Open the recording in Innermaze before importing its Aiko SRT');
  return candidate;
}
function innermazeAikoDuration(plugin, file) {
  return new Promise((resolve,reject)=>{
    const audio=document.createElement('audio');
    audio.preload='metadata';
    let done=false;
    const finish=(error,value)=>{
      if(done)return;
      done=true;clearTimeout(timer);
      audio.removeEventListener('loadedmetadata',loaded);
      audio.removeEventListener('error',failed);
      audio.removeAttribute('src');
      // Release file handle in iOS WebView.
      if(typeof audio.load==='function')audio.load();
      if(error)reject(error);else resolve(value);
    };
    const loaded=()=>{
      const seconds=audio.duration;
      if(!Number.isFinite(seconds)||seconds<=0)
        finish(new Error('Audio duration unavailable; download the Cabinet attachment first'));
      else finish(null,seconds);
    };
    const failed=()=>finish(new Error('Cannot read recording duration; download audio from iCloud first'));
    const timer=setTimeout(()=>finish(new Error('Timed out reading iCloud audio duration')),15000);
    audio.addEventListener('loadedmetadata',loaded);
    audio.addEventListener('error',failed);
    audio.src=plugin.app.vault.getResourcePath(file);
    audio.load();
  });
}
class InnermazeAikoReplaceModal extends Modal {
  constructor(app,decide){super(app);this.decide=decide;this.resolved=false;}
  onOpen(){
    this.setTitle('Existing transcript detected');
    this.contentEl.createEl('p',{text:'This recording already has transcript text or a timing sidecar. Replacing it is optional. The current note and sidecar will be backed up before any replacement; the audio and recording ID will not change.'});
    new Setting(this.contentEl).addButton(b=>b.setButtonText('Cancel').onClick(()=>this.finish(false)))
      .addButton(b=>b.setButtonText('Back up and replace transcript').setWarning()
        .onClick(()=>this.finish(true)));
  }
  finish(value){if(!this.resolved){this.resolved=true;this.decide(value);}this.close();}
  onClose(){if(!this.resolved){this.resolved=true;this.decide(false);}this.contentEl.empty();}
}
function innermazeAikoConfirmReplace(app) {
  return new Promise(resolve=>new InnermazeAikoReplaceModal(app,resolve).open());
}
async function innermazeAikoSaveBackups(plugin, id, noteRaw, syncRaw) {
  const folder=`${NOTE_DIR}/Transcription Backups`;
  await ensureFolder(plugin.app,folder);
  // Contents saved as .txt so backups are never mistaken for live recording notes.
  const stem=`${id.replace(/[^A-Za-z0-9-]/g,'_')}-${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
  const notePath=`${folder}/${stem}.note.txt`,syncPath=`${folder}/${stem}.sync.json.txt`;
  if(plugin.app.vault.getAbstractFileByPath(notePath) || plugin.app.vault.getAbstractFileByPath(syncPath))
    throw new Error('Backup destination already exists; transcript was not changed');
  await plugin.app.vault.create(notePath,noteRaw);
  if(syncRaw!==null)await plugin.app.vault.create(syncPath,syncRaw);
  return {notePath,syncPath:syncRaw===null?null:syncPath};
}
async function innermazeAikoImportSelected(plugin, note, file, options={}) {
  if(!file || !/\.srt$/i.test(String(file.name)) || file.size>4*1024*1024)
    throw new Error('Choose an Aiko .srt export smaller than 4 MiB');
  if(!(note instanceof TFile) || note.extension!=='md')throw new Error('Recording note not found');
  const vault=plugin.app.vault;
  const original=await vault.read(note);
  if(!isRecordingNote(note,original))throw new Error('Selected note is not a verified Innermaze recording');
  const {fields}=parseNote(original);
  const audioFile=vault.getAbstractFileByPath(fields.voice_audio);
  if(!(audioFile instanceof TFile))throw new Error('Listening attachment is unavailable in Cabinet');
  if(!fields.voice_sync.startsWith(`${SYNC_DIR}/`) || fields.voice_sync.includes('..'))
    throw new Error('Unexpected recording timing-sidecar path');
  const existingSync=vault.getAbstractFileByPath(fields.voice_sync);
  if(existingSync && !(existingSync instanceof TFile))throw new Error('Timing sidecar is not a file');
  const existingRaw=existingSync ? await vault.read(existingSync) : null;
  const header=original.match(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/);
  if(!header)throw new Error('Missing recording YAML header');
  const priorBody=original.slice(header[0].length).trim();
  const alreadyReady=/^transcription_status:\s*['"]?ready['"]?\s*$/m.test(header[0]);
  const conflict=Boolean(priorBody || existingSync || alreadyReady);
  const duration=await innermazeAikoDuration(plugin,audioFile);
  const content=await file.text();
  const blocks=innermazeParseAikoSrt(content,duration);
  const result=innermazeAikoSidecar(fields.recording_id,blocks,duration,file.name);
  const updated=innermazeAikoUpdateNote(original,blocks);
  if(conflict){
    const allow=options.confirmReplace ? await options.confirmReplace() : await innermazeAikoConfirmReplace(plugin.app);
    if(!allow)return {status:'cancelled'};
  }
  // Fail closed if another device modified the recording while choosing the SRT.
  if(vault.getAbstractFileByPath(note.path)!==note || await vault.read(note)!==original)
    throw new Error('Recording changed while importing Aiko SRT; nothing overwritten');
  const currentSync=vault.getAbstractFileByPath(fields.voice_sync);
  if((existingSync?currentSync!==existingSync:Boolean(currentSync)) ||
     (currentSync && await vault.read(currentSync)!==existingRaw))
    throw new Error('Timing sidecar changed while importing; nothing overwritten');
  if(conflict)await innermazeAikoSaveBackups(plugin,fields.recording_id,original,existingRaw);
  const syncText=JSON.stringify(result,null,2)+'\n';
  if(existingSync)await vault.modify(existingSync,syncText);
  else await vault.create(fields.voice_sync,syncText);
  try{await vault.modify(note,updated);}
  catch(error){
    if(existingSync){
      try{if(await vault.read(existingSync)===syncText)await vault.modify(existingSync,existingRaw);}
      catch(rollbackError){console.error('Innermaze: sidecar restore failed; backup retained',rollbackError);}
    }
    throw error;
  }
  return {status:'imported',note:note.path,recording_id:fields.recording_id,
    captions:blocks.length,source:'aiko',timing_granularity:'segment'};
}
function innermazeAikoChooseSrt(plugin){
  let note;
  try{note=innermazeAikoCurrentNote(plugin);}
  catch(error){new Notice('Aiko import: '+error.message,10000);return;}
  const input=document.createElement('input');
  input.type='file';input.accept='.srt,text/plain';
  input.addEventListener('change',async()=>{
    const file=input.files?.[0];if(!file)return;
    try{
      const result=await innermazeAikoImportSelected(plugin,note,file);
      if(result.status==='imported')
        new Notice(`Aiko: imported ${result.captions} timed captions; original recording preserved.`,9000);
    }catch(error){new Notice('Aiko SRT import stopped: '+(error.message||error),15000);}
  },{once:true});
  input.click();
}
