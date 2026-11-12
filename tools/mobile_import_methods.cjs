// filename: mobile_import_methods.cjs
// version: 0.2.0
// timestamp: 2026-10-27 14:38:00
// Agent B: complete iOS import implementations. Injected into main.js by candidate builder.
// Requires the existing Innermaze globals: TFile, Notice, ImportProgress, safeTitle,
// sha256Buffer, ensureFolder, waitTick, NOTE_DIR, LEGACY_NOTE_DIR, AUDIO_DIR,
// SYNC_DIR, PICKER_MAX_BYTES, isAudioName, encodeYaml, plugin.app.

async function innermazeMobileSnapshots(app) {
  const notes = [];
  for (const file of app.vault.getMarkdownFiles()) {
    const folder = file.parent?.path || file.path.slice(0, file.path.lastIndexOf('/'));
    if (folder !== NOTE_DIR && folder !== LEGACY_NOTE_DIR) continue;
    notes.push({path: file.path, source: await app.vault.read(file)});
  }
  return notes;
}

async function innermazePlanMobile(plugin, digest, appleUuid) {
  return innermazeMobileIdentity.planMobileImport(
    {fileSha256: digest, appleUuid}, await innermazeMobileSnapshots(plugin.app));
}

function makeGenericNote(id, title, audioPath, digest, appleUuid = null) {
  const fields = [
    '---',
    'voice_archive: true',
    `recording_id: ${encodeYaml(id)}`,
    `recording_source: ${encodeYaml(appleUuid ? 'apple-voice-memo' : 'arbitrary-file')}`,
    `source_title: ${encodeYaml(title)}`,
    `display_title: ${encodeYaml(innermazeMobileDisplayTitle(title))}`,
    `voice_audio: ${encodeYaml(audioPath)}`,
    `voice_sync: ${encodeYaml(`${SYNC_DIR}/${id}.sync.json`)}`,
    `source_sha256: ${encodeYaml(digest)}`,
    `playback_sha256: ${encodeYaml(digest)}`,
    'transcription_status: "pending"',
    'tags:',
    '  - voice-archive',
    '---',
    ''
  ];
  return fields.join('\n') + '\n';
}

async function makeNoteAndOpen(plugin, title, audioPath, digest, progress, appleUuid = null) {
  progress.step(5, 'Verifying immutable recording identity');
  // Re-scan after any asynchronous iCloud audio copy to avoid stale conclusions.
  const plan = await innermazePlanMobile(plugin, digest, appleUuid);
  if (plan.action === 'review_required') {
    throw new Error(`Recording identity needs review: ${plan.reason}. No note was changed.`);
  }
  let note;
  if (plan.action === 'reuse_note') {
    note = plugin.app.vault.getAbstractFileByPath(plan.notePath);
    if (!(note instanceof TFile)) throw new Error('Verified existing recording note is unavailable.');
    // No note changes on reuse: links, renamed paths, titles, transcripts and
    // existing playback/resume positions all remain exactly as they were.
  } else if (plan.action === 'create_note') {
    if (!audioPath) throw new Error('New recording has no verified listening asset.');
    const audio = plugin.app.vault.getAbstractFileByPath(audioPath);
    if (!(audio instanceof TFile)) throw new Error('Listening asset unavailable; refusing orphan note.');
    await ensureFolder(plugin.app, NOTE_DIR);
    const notePath = innermazeMobileUniqueNotePath(plugin.app.vault, title, plan.recordingId);
    if (plugin.app.vault.getAbstractFileByPath(notePath))
      throw new Error(`Existing note destination requires review: ${notePath}`);
    note = await plugin.app.vault.create(notePath, makeGenericNote(
      plan.recordingId, safeTitle(title), audioPath, digest, appleUuid));
  } else {
    throw new Error(`Unexpected recording identity decision: ${plan.action}`);
  }
  progress.step(6, 'Complete: recording note preserved or queued');
  await plugin.app.workspace.getLeaf(true).openFile(note);
  new Notice(plan.action === 'reuse_note' ?
    'Already imported: existing recording note opened unchanged.' :
    'Audio imported. Transcription is available on Mango.');
  return note;
}

function innermazeEmbeddedAppleUuid(bytes, name) {
  const ext = String(name).split('.').pop().toLowerCase();
  // Other codecs are not known to carry Apple MP4 metadata. A missing UUID
  // never gets guessed from the title, iOS export filename, date or audio hash.
  return ['m4a', 'mp4', 'm4b'].includes(ext) ?
    innermazeMobileIdentity.parseAppleVoiceMemoUuid(bytes) : null;
}

async function innermazeVerifiedManagedAudio(plugin, digest) {
  const matches = plugin.app.vault.getFiles().filter(
    f => f.path.startsWith(`${AUDIO_DIR}/sha256-${digest}.`));
  for (const file of matches) {
    const bytes = await plugin.app.vault.readBinary(file);
    if (await sha256Buffer(bytes) !== digest)
      throw new Error(`Hash-named listening asset has unexpected contents: ${file.path}`);
  }
  return matches.length ? matches[0].path : null;
}

async function importPickedAudio(plugin) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'audio/*,.m4a,.mp3,.wav,.aif,.aiff,.caf,.flac,.ogg,.opus,.aac,.webm,.mp4,.m4b,.wma,.amr';
  input.addEventListener('change', async () => {
    const files = Array.from(input.files || []);
    if (!files.length) return;
    for (const file of files) {
      const progress = new ImportProgress(plugin.app);
      progress.open();
      try {
        progress.step(1, `Reading ${file.name}`); await waitTick();
        if (!isAudioName(file.name)) throw new Error(`Not a supported audio extension: ${file.name}`);
        if (file.size > PICKER_MAX_BYTES)
          throw new Error('Over 200 MiB: use the desktop importer to avoid exhausting mobile memory.');
        const bytes = await file.arrayBuffer();
        progress.step(2, 'Reading Apple identity and verifying SHA-256'); await waitTick();
        const digest = await sha256Buffer(bytes);
        const appleUuid = innermazeEmbeddedAppleUuid(bytes, file.name);
        const plan = await innermazePlanMobile(plugin, digest, appleUuid);
        if (plan.action === 'review_required')
          throw new Error(`Recording identity needs review: ${plan.reason}. No import was written.`);
        let audioPath = null;
        if (plan.action === 'create_note') {
          progress.step(3, 'Preparing verified iCloud listening asset'); await waitTick();
          await ensureFolder(plugin.app, AUDIO_DIR);
          audioPath = await innermazeVerifiedManagedAudio(plugin, digest);
          if (!audioPath) {
            const ext = file.name.split('.').pop().toLowerCase();
            audioPath = `${AUDIO_DIR}/sha256-${digest}.${ext}`;
            const other = plugin.app.vault.getAbstractFileByPath(audioPath);
            if (other) throw new Error('Audio path already exists but is not a verified managed asset.');
            await plugin.app.vault.createBinary(audioPath, bytes);
          }
        } else {
          progress.step(3, 'Reusing existing recording without audio duplication'); await waitTick();
        }
        progress.step(4, 'Validating recording note');
        await makeNoteAndOpen(plugin, file.name, audioPath, digest, progress, appleUuid);
      } catch (error) {
        new Notice(`Audio import stopped: ${error.message || error}`, 12000);
      } finally { progress.close(); }
    }
  }, {once: true});
  input.click();
}

async function importVaultAudio(plugin, file) {
  const progress = new ImportProgress(plugin.app);
  progress.open();
  try {
    progress.step(1, 'Reading existing vault audio'); await waitTick();
    if (file.stat.size > PICKER_MAX_BYTES)
      throw new Error('Over 200 MiB: use the desktop importer for streaming SHA-256.');
    const bytes = await plugin.app.vault.readBinary(file);
    progress.step(2, 'Checking recording identity'); await waitTick();
    const digest = await sha256Buffer(bytes);
    const appleUuid = innermazeEmbeddedAppleUuid(bytes, file.name);
    const preview = await innermazePlanMobile(plugin, digest, appleUuid);
    if (preview.action === 'review_required')
      throw new Error(`Recording identity needs review: ${preview.reason}. No note was changed.`);
    progress.step(3, 'Reusing existing vault audio');
    progress.step(4, 'Checking existing notes');
    await makeNoteAndOpen(plugin, file.name, file.path, digest, progress, appleUuid);
  } catch (error) {
    new Notice(`Audio import stopped: ${error.message || error}`, 12000);
  } finally { progress.close(); }
}
