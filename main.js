// filename: main.js
// version: 0.1.1
// timestamp: 2026-10-24 10:53:00
// Obsidian Innermaze: offline audio + editable plain Markdown + external timing.
'use strict';

const { Plugin, ItemView, Notice, TFile, Modal } = require('obsidian');
const VIEW_TYPE = 'innermaze-player';
const AUDIO_DIR = 'Voice Archive/Audio';
const NOTE_DIR = 'Voice Archive/Notes';
const SYNC_DIR = 'Voice Archive/Sync';
const PICKER_MAX_BYTES = 200 * 1024 * 1024; // Desktop CLI handles larger files without buffering.
// Timed-word gaps are a speech heuristic, not an acoustic silence detector.
// Keep the original file unchanged and pad both sides so speech is not clipped.
const MIN_PAUSE_SECONDS = 2.0;
const PAUSE_PADDING_SECONDS = 0.4;
const AUDIO_EXTENSIONS = new Set(['m4a', 'mp3', 'wav', 'aif', 'aiff', 'caf', 'flac', 'ogg', 'opus', 'aac', 'webm', 'mp4', 'm4b', 'wma', 'amr']);

function decodeYamlScalar(s) {
  try { return JSON.parse(s.trim()); } catch (_) { return s.trim().replace(/^['"]|['"]$/g, ''); }
}

function parseNote(source) {
  const match = source.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!match) throw new Error('Voice Archive note has no YAML frontmatter.');
  const fields = {};
  for (const line of match[1].split(/\r?\n/)) {
    const found = line.match(/^(voice_audio|voice_sync|recording_id|display_title):\s*(.*)$/);
    if (found) fields[found[1]] = decodeYamlScalar(found[2]);
  }
  if (!fields.voice_audio || !fields.voice_sync) {
    throw new Error('This note needs voice_audio and voice_sync properties.');
  }
  const body = source.slice(match[0].length).trim();
  const paragraphs = body ? body.split(/\r?\n\s*\r?\n+/).map(x => x.trim()).filter(Boolean) : [];
  return { fields, paragraphs };
}

function clean(s) {
  return String(s || '').toLocaleLowerCase().normalize('NFKC').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}

function similarity(left, right) {
  const a = clean(left), b = clean(right);
  if (!a || !b) return 0;
  if (a === b) return 1;
  // Token overlap tolerates punctuation, name spelling and corrected words.
  const aa = a.split(' '), bb = b.split(' ');
  const counts = new Map();
  for (const word of aa) counts.set(word, (counts.get(word) || 0) + 1);
  let common = 0;
  for (const word of bb) {
    if (counts.get(word) > 0) { common++; counts.set(word, counts.get(word) - 1); }
  }
  return (2 * common) / (aa.length + bb.length);
}

function alignParagraphs(paragraphs, blocks) {
  const n = paragraphs.length, m = blocks.length;
  if (n === m) {
    // Same paragraph count: ordinary spelling changes keep their existing time.
    // Exact text moved to another position still follows its original anchor.
    const counts = new Map();
    const index = new Map();
    for (let j = 0; j < m; j++) {
      const key = clean(blocks[j].text);
      counts.set(key, (counts.get(key) || 0) + 1);
      index.set(key, j);
    }
    const result = Array(n).fill(-1), claimed = new Set();
    for (let i = 0; i < n; i++) {
      const key = clean(paragraphs[i]);
      if (counts.get(key) === 1) { const j = index.get(key); result[i] = j; claimed.add(j); }
    }
    for (let i = 0; i < n; i++) {
      if (result[i] < 0 && !claimed.has(i)) { result[i] = i; claimed.add(i); }
    }
    for (let i = 0; i < n; i++) {
      if (result[i] < 0) {
        const best = blocks.findIndex((_, j) => !claimed.has(j));
        if (best >= 0) { result[i] = best; claimed.add(best); }
      }
    }
    return result;
  }
  // Global monotonic alignment. Inserted prose remains UNALIGNED, never given invented times.
  // Bound memory for very long notes: sequential fallback is conservative.
  if (n * m > 500000) {
    const result = Array(n).fill(-1);
    let cursor = 0;
    for (let i = 0; i < n; i++) {
      let best = -1, bestScore = 0.45;
      for (let j = cursor; j < Math.min(m, cursor + 7); j++) {
        const score = similarity(paragraphs[i], blocks[j].text);
        if (score > bestScore) { best = j; bestScore = score; }
      }
      if (best >= 0) { result[i] = best; cursor = best + 1; }
    }
    return result;
  }
  const dp = Array.from({ length: n + 1 }, () => new Float64Array(m + 1));
  const route = Array.from({ length: n + 1 }, () => new Uint8Array(m + 1));
  for (let i = 1; i <= n; i++) { dp[i][0] = i * 0.65; route[i][0] = 1; }
  for (let j = 1; j <= m; j++) { dp[0][j] = j * 0.65; route[0][j] = 2; }
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      const score = similarity(paragraphs[i - 1], blocks[j - 1].text);
      let best = dp[i - 1][j - 1] + (score < 0.24 ? 1.5 : 1.0 - score);
      let choice = 0;
      if (dp[i - 1][j] + 0.65 < best) { best = dp[i - 1][j] + 0.65; choice = 1; }
      if (dp[i][j - 1] + 0.65 < best) { best = dp[i][j - 1] + 0.65; choice = 2; }
      dp[i][j] = best;
      route[i][j] = choice;
    }
  }
  const alignment = Array(n).fill(-1);
  let i = n, j = m;
  while (i > 0 || j > 0) {
    const choice = route[i][j];
    if (i > 0 && j > 0 && choice === 0) {
      if (similarity(paragraphs[i - 1], blocks[j - 1].text) >= 0.24) alignment[i - 1] = j - 1;
      i--; j--;
    } else if (i > 0 && (j === 0 || choice === 1)) i--;
    else j--;
  }
  // Recover uniquely moved, verbatim paragraphs missed by monotonic matching.
  const used = new Set(alignment.filter(j => j >= 0));
  for (let k = 0; k < n; k++) {
    if (alignment[k] >= 0) continue;
    let found = -1;
    for (let j2 = 0; j2 < m; j2++) {
      if (!used.has(j2) && clean(paragraphs[k]) === clean(blocks[j2].text)) {
        if (found >= 0) { found = -1; break; }
        found = j2;
      }
    }
    if (found >= 0) { alignment[k] = found; used.add(found); }
  }
  return alignment;
}

// Produce seekable intervals from existing word/segment timing. Never cut audio bytes.
// The sorter and overlap coalescing accommodate ASR timestamps that are out of order.
function pauseSkipRanges(words, minGap = MIN_PAUSE_SECONDS, padding = PAUSE_PADDING_SECONDS) {
  if (!Array.isArray(words) || !Number.isFinite(minGap) || minGap <= 0 ||
      !Number.isFinite(padding) || padding < 0) return [];
  const timed = words
    .filter(w => w && Number.isFinite(w.start) && Number.isFinite(w.end) &&
      w.start >= 0 && w.end > w.start && String(w.text || '').trim())
    .map(w => ({ start: w.start, end: w.end }))
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const gaps = [];
  if (!timed.length) return gaps;
  let speechEnd = timed[0].end;
  for (let i = 1; i < timed.length; i++) {
    const word = timed[i];
    const gap = word.start - speechEnd;
    if (gap >= minGap) {
      const from = speechEnd + padding, to = word.start - padding;
      if (to - from >= 0.35) gaps.push({ from, to });
    }
    speechEnd = Math.max(speechEnd, word.end);
  }
  return gaps;
}

function pauseSkipDestination(ranges, time) {
  if (!Array.isArray(ranges) || !Number.isFinite(time) || !ranges.length) return null;
  // Binary search to avoid scanning thousands of intervals each timeupdate.
  let lo = 0, hi = ranges.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (ranges[mid].from <= time) lo = mid + 1;
    else hi = mid;
  }
  if (!lo) return null;
  const gap = ranges[lo - 1];
  // No skip during the audible padding at either side of the speech gap.
  return time < gap.to - 0.08 ? gap.to : null;
}

// The last spoken line remains highlighted during any silence, and the next
// line takes over when its speech starts. Text order may differ after editing.
function highlightedLineIndex(lines, time) {
  if (!Array.isArray(lines) || !Number.isFinite(time)) return -1;
  let firstIndex = -1, firstStart = Infinity;
  let activeIndex = -1, activeStart = -Infinity;
  for (let i = 0; i < lines.length; i++) {
    const block = lines[i]?.anchor;
    if (!block || !Number.isFinite(block.start) || block.start < 0) continue;
    if (block.start < firstStart) { firstStart = block.start; firstIndex = i; }
    if (block.start <= time + 0.02 && block.start >= activeStart) {
      activeStart = block.start; activeIndex = i;
    }
  }
  return activeIndex >= 0 ? activeIndex : firstIndex;
}

function formatTime(seconds) {
  if (!Number.isFinite(seconds)) return '--:--';
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return (h ? `${h}:${String(m).padStart(2, '0')}:` : `${m}:`) + String(s % 60).padStart(2, '0');
}

class InnermazeView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.notePath = '';
    this.currentAudio = '';
    this.lines = [];
    this.pauseRanges = [];
    this.lastHighlight = -1;
    this.loadSequence = 0;
    this.lastSavedAt = 0;
    this.statusText = "";
  }
  getViewType() { return VIEW_TYPE; }
  getDisplayText() { return 'Innermaze'; }
  getIcon() { return 'audio-lines'; }

  async onOpen() {
    const root = this.contentEl;
    root.empty(); root.addClass('vap-root');
    const header = root.createDiv({ cls: 'vap-header' });
    this.titleEl = header.createEl('h3', { text: 'Innermaze' });
    const edit = header.createEl('button', { text: 'Edit transcript' });
    edit.addEventListener('click', async () => {
      if (this.notePath) await this.app.workspace.openLinkText(this.notePath, '', true);
    });
    this.audio = root.createEl('audio', { attr: { controls: '', preload: 'metadata' } });
    this.audio.classList.add('vap-audio');
    const tools = root.createDiv({ cls: 'vap-tools' });
    const back = tools.createEl('button', { text: '−10s', attr: { 'aria-label': 'Back ten seconds' } });
    back.addEventListener('click', () => { this.audio.currentTime = Math.max(0, this.audio.currentTime - 10); });
    const forward = tools.createEl('button', { text: '+10s', attr: { 'aria-label': 'Forward ten seconds' } });
    forward.addEventListener('click', () => { this.audio.currentTime = Math.min(this.audio.duration || Infinity, this.audio.currentTime + 10); });
    tools.createSpan({ text: 'Speed' });
    const speed = tools.createEl('select');
    for (const rate of [0.75, 1, 1.25, 1.5, 1.75, 2]) {
      const opt = speed.createEl('option', { text: `${rate}×`, value: String(rate) });
      if (rate === 1) opt.selected = true;
    }
    speed.addEventListener('change', () => { this.audio.playbackRate = Number(speed.value); });
    const gapToggle = tools.createEl('label', { cls: 'vap-gap-control' });
    gapToggle.setAttribute('title', 'Jump over long gaps between transcribed words; original audio is unchanged.');
    this.gapCheckbox = gapToggle.createEl('input', { type: 'checkbox' });
    this.gapCheckbox.checked = this.plugin.data.skipPauses !== false;
    gapToggle.createSpan({ text: 'Skip long pauses' });
    this.gapCheckbox.addEventListener('change', () => {
      this.plugin.data.skipPauses = this.gapCheckbox.checked;
      void this.plugin.saveData(this.plugin.data).catch(error =>
        new Notice(`Unable to save pause setting: ${error.message || error}`));
    });
    this.info = tools.createSpan({ cls: 'vap-info' });
    this.scrollEl = root.createDiv({ cls: 'vap-scroll' });
    this.audio.addEventListener('timeupdate', () => {
      this.skipPauseIfNeeded();
      this.updateHighlight();
      if (this.audio.currentTime - this.lastSavedAt >= 15) this.storeResume();
    });
    this.audio.addEventListener('seeked', () => { this.updateHighlight(); });
    this.audio.addEventListener('pause', () => { void this.storeResume(); });
    this.audio.addEventListener('ended', () => { void this.storeResume(true); });
    this.audio.addEventListener('loadedmetadata', () => {
      const saved = this.plugin.data.positions?.[this.notePath] || 0;
      if (saved > 0 && Number.isFinite(this.audio.duration) && saved < this.audio.duration - 2) this.audio.currentTime = saved;
    });
    if (this.notePath) await this.loadRecording(this.notePath);
  }

  async setRecordingPath(path) {
    this.notePath = path;
    if (this.audio) await this.loadRecording(path);
  }

  async loadRecording(path) {
    const current = ++this.loadSequence;
    this.pauseRanges = [];
    const note = this.app.vault.getAbstractFileByPath(path);
    if (!(note instanceof TFile)) { new Notice('Voice Archive note not found.'); return; }
    try {
      const { fields } = parseNote(await this.app.vault.read(note));
      const audioFile = this.app.vault.getAbstractFileByPath(fields.voice_audio);
      if (!(audioFile instanceof TFile)) throw new Error(`Audio missing: ${fields.voice_audio}`);
      if (current !== this.loadSequence) return;
      this.titleEl.setText(fields.display_title || note.basename);
      this.notePath = path;
      if (this.currentAudio !== audioFile.path) {
        this.currentAudio = audioFile.path;
        this.audio.pause();
        this.audio.src = this.app.vault.getResourcePath(audioFile);
        this.audio.load();
      }
      await this.refreshTranscript();
    } catch (error) { this.info.setText(String(error.message || error)); new Notice(String(error.message || error)); }
  }

  async refreshTranscript() {
    if (!this.notePath || !this.scrollEl) return;
    const current = ++this.loadSequence;
    this.pauseRanges = [];
    try {
      const note = this.app.vault.getAbstractFileByPath(this.notePath);
      if (!(note instanceof TFile)) return;
      const parsed = parseNote(await this.app.vault.read(note));
      this.titleEl.setText(parsed.fields.display_title || note.basename);
      const syncFile = this.app.vault.getAbstractFileByPath(parsed.fields.voice_sync);
      if (!(syncFile instanceof TFile)) {
        this.pauseRanges = [];
        this.lines = [];
        this.scrollEl.empty();
        this.scrollEl.createDiv({cls: 'vap-pending', text: 'Awaiting local transcription on Mango. Audio is already available for playback.'});
        this.info.setText('Transcription pending');
        return;
      }
      const sidecar = JSON.parse(await this.app.vault.read(syncFile));
      if (sidecar.format !== 'voice-archive-sync-v1' || !Array.isArray(sidecar.blocks)) throw new Error('Unsupported sync format.');
      if (sidecar.recording_id !== parsed.fields.recording_id) throw new Error('Recording identity mismatch.');
      if (current !== this.loadSequence) return;
      const blocks = sidecar.blocks.filter(b => Number.isFinite(b.start) && Number.isFinite(b.end) && b.end >= b.start);
      // Word timestamps permit gap skipping even inside one Markdown paragraph.
      // If the sidecar contains no valid word timings, conservatively fall back
      // to line intervals instead. No timestamps means no skipping.
      const pauseWords = Array.isArray(sidecar.words) && sidecar.words.length
        ? sidecar.words : blocks;
      this.pauseRanges = pauseSkipRanges(pauseWords);
      const alignment = alignParagraphs(parsed.paragraphs, blocks);
      this.lines = [];
      this.scrollEl.empty();
      let matched = 0;
      for (let i = 0; i < parsed.paragraphs.length; i++) {
        const anchor = alignment[i] >= 0 ? blocks[alignment[i]] : null;
        const el = this.scrollEl.createDiv({ cls: 'vap-line' });
        if (anchor) {
          matched++;
          const time = el.createSpan({ cls: 'vap-time', text: formatTime(anchor.start) });
          time.setAttribute('aria-hidden', 'true');
          el.addClass('vap-linked');
          el.setAttribute('role', 'button');
          el.setAttribute('tabindex', '0');
          el.setAttribute('aria-label', `Play at ${formatTime(anchor.start)}`);
          const seek = () => { this.audio.currentTime = anchor.start; void this.audio.play().catch(() => {}); };
          el.addEventListener('click', seek);
          el.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); seek(); }
          });
        } else {
          el.addClass('vap-unaligned');
          el.setAttribute('title', 'Unaligned: paragraph changed structure; not assigned an invented timestamp');
        }
        el.createSpan({ cls: 'vap-words', text: parsed.paragraphs[i] });
        this.lines.push({ el, anchor });
      }
      this.info.setText(`${matched}/${parsed.paragraphs.length} lines aligned` + (matched !== parsed.paragraphs.length ? ' · review unaligned lines' : ''));
      this.lastHighlight = -1;
      this.updateHighlight();
    } catch (error) { this.info.setText(`Transcript error: ${error.message || error}`); }
  }

  skipPauseIfNeeded() {
    if (!this.audio || this.audio.paused || this.audio.seeking ||
        this.plugin.data.skipPauses === false || !this.pauseRanges.length) return;
    const target = pauseSkipDestination(this.pauseRanges, this.audio.currentTime);
    if (target !== null && (!Number.isFinite(this.audio.duration) || target < this.audio.duration)) {
      this.audio.currentTime = target;
    }
  }

  updateHighlight() {
    if (!this.audio || !this.lines.length) return;
    const selected = highlightedLineIndex(this.lines, this.audio.currentTime);
    if (selected === this.lastHighlight) return;
    if (this.lastHighlight >= 0 && this.lines[this.lastHighlight]) this.lines[this.lastHighlight].el.removeClass('vap-active');
    if (selected >= 0) {
      const el = this.lines[selected].el;
      el.addClass('vap-active');
      if (!this.audio.paused) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    this.lastHighlight = selected;
  }

  async storeResume(ended = false) {
    if (!this.notePath || !this.audio) return;
    this.lastSavedAt = this.audio.currentTime;
    this.plugin.data.positions[this.notePath] = ended ? 0 : this.audio.currentTime;
    await this.plugin.saveData(this.plugin.data);
  }
  async onClose() { if (this.audio) { this.audio.pause(); await this.storeResume(); } }
}

function isAudioName(name) {
  return AUDIO_EXTENSIONS.has(String(name).split('.').pop().toLowerCase());
}
function encodeYaml(value) { return JSON.stringify(String(value)); }
function safeTitle(value) { return String(value).replace(/[\r\n]+/g, ' ').trim() || 'Untitled Audio'; }
function waitTick() { return new Promise(resolve => setTimeout(resolve, 0)); }

class ImportProgress extends Modal {
  onOpen() {
    this.contentEl.empty();
    this.contentEl.createEl('h3', {text: 'Import audio'});
    this.statusEl = this.contentEl.createDiv({text: 'Preparing…'});
    this.bar = this.contentEl.createEl('progress', {attr: {max: '6', value: '0'}});
    this.bar.style.width = '100%';
  }
  step(current, message) {
    if (!this.bar) return;
    this.bar.value = current;
    this.statusEl.setText(`${current}/6 · ${message}`);
  }
}

async function sha256Buffer(bytes) {
  if (!globalThis.crypto || !globalThis.crypto.subtle) {
    throw new Error('SHA-256 is unavailable in this Obsidian runtime. Use the desktop importer.');
  }
  const result = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(result), n => n.toString(16).padStart(2, '0')).join('');
}

async function ensureFolder(app, path) {
  const parts = path.split('/');
  let current = '';
  for (const segment of parts) {
    current = current ? current + '/' + segment : segment;
    if (!app.vault.getAbstractFileByPath(current)) await app.vault.createFolder(current);
  }
}

function makeGenericNote(id, title, audioPath, digest) {
  const fields = [
    '---',
    'voice_archive: true',
    `recording_id: ${encodeYaml(id)}`,
    `recording_source: "arbitrary-file"`,
    `source_title: ${encodeYaml(title)}`,
    `display_title: ${encodeYaml(title)}`,
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

async function makeNoteAndOpen(plugin, title, audioPath, digest, progress) {
  const id = `audio-${digest}`;
  const notePath = `${NOTE_DIR}/${id}.md`;
  progress.step(5, 'Creating editable transcript note');
  await ensureFolder(plugin.app, NOTE_DIR);
  const existing = plugin.app.vault.getAbstractFileByPath(notePath);
  let note = existing;
  if (!note) note = await plugin.app.vault.create(notePath, makeGenericNote(id, safeTitle(title), audioPath, digest));
  if (!(note instanceof TFile)) throw new Error('Import note path exists but is not a file.');
  progress.step(6, 'Complete: queued for local Mango transcription');
  await plugin.app.workspace.getLeaf(true).openFile(note);
  new Notice('Audio is in your iCloud vault. Transcription will run on Mango.');
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
        if (file.size > PICKER_MAX_BYTES) {
          throw new Error('Over 200 MiB: use the desktop Python importer to avoid exhausting Obsidian mobile memory.');
        }
        const bytes = await file.arrayBuffer();
        progress.step(2, 'Computing identity'); await waitTick();
        const digest = await sha256Buffer(bytes);
        await ensureFolder(plugin.app, AUDIO_DIR);
        const candidate = plugin.app.vault.getFiles().find(f => f.path.startsWith(`${AUDIO_DIR}/sha256-${digest}.`));
        let audioPath = candidate?.path;
        progress.step(3, candidate ? 'Identical audio found; reusing it' : 'Writing one iCloud playback copy'); await waitTick();
        if (!audioPath) {
          const ext = file.name.split('.').pop().toLowerCase();
          audioPath = `${AUDIO_DIR}/sha256-${digest}.${ext}`;
          const current = plugin.app.vault.getAbstractFileByPath(audioPath);
          if (!current) await plugin.app.vault.createBinary(audioPath, bytes);
          else if (!(current instanceof TFile)) throw new Error('Audio destination is a folder.');
        }
        progress.step(4, 'Audio imported');
        await makeNoteAndOpen(plugin, file.name, audioPath, digest, progress);
      } catch (error) { new Notice(`Audio import stopped: ${error.message || error}`, 12000); }
      finally { progress.close(); }
    }
  }, {once: true});
  input.click();
}

async function importVaultAudio(plugin, file) {
  const progress = new ImportProgress(plugin.app);
  progress.open();
  try {
    progress.step(1, 'Reading existing vault audio'); await waitTick();
    if (file.stat.size > PICKER_MAX_BYTES) {
      throw new Error('Over 200 MiB: use the desktop Python importer for streaming SHA-256.');
    }
    const bytes = await plugin.app.vault.readBinary(file);
    progress.step(2, 'Hashing existing attachment'); await waitTick();
    const digest = await sha256Buffer(bytes);
    progress.step(3, 'Reusing vault file: zero copying');
    progress.step(4, 'Checking existing notes');
    await makeNoteAndOpen(plugin, file.name, file.path, digest, progress);
  } catch (error) { new Notice(`Audio import stopped: ${error.message || error}`, 12000); }
  finally { progress.close(); }
}

module.exports = class InnermazePlugin extends Plugin {
  async onload() {
    this.data = Object.assign({ positions: {}, skipPauses: true }, await this.loadData() || {});
    if (!this.data.positions) this.data.positions = {};
    this.registerView(VIEW_TYPE, leaf => new InnermazeView(leaf, this));
    this.addCommand({
      id: 'open-innermaze-player',
      name: 'Open player for current voice recording',
      callback: async () => {
        const note = this.app.workspace.getActiveFile();
        if (!note) { new Notice('Open a Voice Archive Markdown note first.'); return; }
        try { parseNote(await this.app.vault.read(note)); }
        catch (_) { new Notice('Current note is not an Innermaze recording.'); return; }
        const leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0] || this.app.workspace.getLeaf('tab');
        if (leaf.getViewState().type !== VIEW_TYPE) await leaf.setViewState({ type: VIEW_TYPE, active: true });
        await leaf.view.setRecordingPath(note.path);
        await this.app.workspace.revealLeaf(leaf);
      }
    });
    this.addCommand({
      id: 'import-audio-file',
      name: 'Import audio from Files or Finder (iCloud playback)',
      callback: () => { void importPickedAudio(this); }
    });
    this.addCommand({
      id: 'use-current-vault-audio',
      name: 'Create player note for current vault audio (no audio copy)',
      callback: async () => {
        const file = this.app.workspace.getActiveFile();
        if (!(file instanceof TFile) || !isAudioName(file.name)) {
          new Notice('Open an audio file within the vault first.'); return;
        }
        await importVaultAudio(this, file);
      }
    });
    this.registerEvent(this.app.vault.on('create', file => {
      if (!file.path.endsWith('.sync.json')) return;
      for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
        if (leaf.view.notePath) void leaf.view.refreshTranscript();
      }
    }));
    this.registerEvent(this.app.vault.on('modify', file => {
      for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
        const view = leaf.view;
        if (view.notePath === file.path || (file.path.endsWith('.sync.json') && view.notePath)) void view.refreshTranscript();
      }
    }));
  }
  async onunload() { /* registered views/listeners are disposed by Obsidian */ }
};

// Exposed exclusively for node-level test harness, not needed by Obsidian.
if (typeof process !== 'undefined' && process.env && process.env.INNERMAZE_TEST === '1') {
  module.exports._internals = { parseNote, alignParagraphs, similarity, formatTime,
    makeGenericNote, isAudioName, pauseSkipRanges, pauseSkipDestination,
    highlightedLineIndex };
}
