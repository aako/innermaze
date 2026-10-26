// filename: main.js
// version: 0.1.5
// timestamp: 2026-10-24 10:53:00
// Obsidian Innermaze: offline audio + editable plain Markdown + external timing.
'use strict';

const { Plugin, PluginSettingTab, Setting, ItemView, Notice, TFile, Modal, MarkdownView, Platform } = require('obsidian');
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

// Only Voice Archive Markdown notes open a player. Do not treat a filename,
// ordinary Markdown note, or unrelated plugin's frontmatter as a recording.
function isRecordingNote(file, source) {
  if (!(file instanceof TFile) || file.extension !== 'md') return false;
  const match = String(source).match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!match || !/^voice_archive:\s*true\s*$/m.test(match[1])) return false;
  try {
    return Boolean(parseNote(source).fields.recording_id);
  } catch (_) {
    return false;
  }
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

// Innermaze 0.1.5 experimental M4A assembly; no decode, transcode, or vault writes.
// Supports ordinary nonfragmented, single-track AAC MP4/M4A. Refuse other layouts.


const BAD = message => { throw new Error('M4A test: ' + message); };
function boxList(a, start = 0, end = a.length) {
  if (!(a instanceof Uint8Array)) BAD('expected Uint8Array');
  if (start < 0 || end > a.length || end < start) BAD('invalid box range');
  const view = new DataView(a.buffer, a.byteOffset, a.byteLength);
  const boxes = [];
  for (let at = start; at < end;) {
    if (at + 8 > end) BAD('incomplete box header');
    let n = view.getUint32(at), header = 8;
    const type = String.fromCharCode(a[at + 4], a[at + 5], a[at + 6], a[at + 7]);
    if (n === 1) {
      if (at + 16 > end) BAD('invalid large-size box');
      const high = view.getUint32(at + 8), low = view.getUint32(at + 12);
      n = high * 4294967296 + low;
      header = 16;
    } else if (n === 0) { n = end - at; }
    if (n < header || at + n > end || !Number.isSafeInteger(n)) BAD('invalid box size for ' + type);
    boxes.push({ type, start: at, end: at + n, header });
    at += n;
  }
  return boxes;
}
function child(a, t) { return boxList(a, 8).find(b => b.type === t); }
function uniqueChild(a, t) {
  const cs = boxList(a, 8).filter(b => b.type === t);
  if (cs.length !== 1) BAD('expected one ' + t + ' box');
  return Uint8Array.from(a.subarray(cs[0].start, cs[0].end));
}
function childMaybe(a, t) {
  const cs = boxList(a, 8).filter(b => b.type === t);
  if (cs.length > 1) BAD('multiple ' + t + ' boxes');
  return cs.length ? Uint8Array.from(a.subarray(cs[0].start, cs[0].end)) : null;
}
function u16(n) { const a = new Uint8Array(2); new DataView(a.buffer).setUint16(0, n); return a; }
function u32(n) { if (!Number.isInteger(n) || n < 0 || n > 0xffffffff) BAD('32-bit overflow'); const a = new Uint8Array(4); new DataView(a.buffer).setUint32(0, n); return a; }
function u64(n) { if (!Number.isSafeInteger(n) || n < 0) BAD('64-bit overflow'); const a = new Uint8Array(8); new DataView(a.buffer).setBigUint64(0, BigInt(n)); return a; }
function s64(n) { if (!Number.isSafeInteger(n)) BAD('signed 64-bit overflow'); const a = new Uint8Array(8); new DataView(a.buffer).setBigInt64(0, BigInt(n)); return a; }
function join(xs) { const size = xs.reduce((n, a) => n + a.length, 0); const out = new Uint8Array(size); let at = 0; for (const x of xs) { out.set(x, at); at += x.length; } return out; }
function makeBox(type, payload) {
  if (type.length !== 4 || payload.length + 8 > 0xffffffff) BAD('invalid generated box');
  return join([u32(payload.length + 8), Uint8Array.from(type, c => c.charCodeAt(0)), payload]);
}
function rebox(parent, edits, drop = []) {
  const boxes = boxList(parent, 8);
  let applied = new Set();
  const out = [];
  for (const b of boxes) {
    if (drop.includes(b.type)) continue;
    if (Object.prototype.hasOwnProperty.call(edits, b.type)) {
      if (applied.has(b.type)) BAD('duplicate ' + b.type);
      out.push(edits[b.type]); applied.add(b.type);
    } else out.push(parent.slice(b.start, b.end));
  }
  for (const [k, val] of Object.entries(edits)) if (!applied.has(k)) out.push(val);
  return makeBox(String.fromCharCode(...parent.subarray(4, 8)), join(out));
}
function dv(a) { return new DataView(a.buffer, a.byteOffset, a.byteLength); }
function readDurationBox(a, type) {
  const v = a[8], view = dv(a);
  const tsOff = v ? 28 : 20, durOff = v ? 32 : 24;
  if (a.length < durOff + (v ? 8 : 4)) BAD('short ' + type);
  const timescale = view.getUint32(tsOff);
  const duration = v ? Number(view.getBigUint64(durOff)) : view.getUint32(durOff);
  if (!timescale || !Number.isSafeInteger(duration)) BAD('invalid ' + type + ' timing');
  return { timescale, duration, version: v, durationOffset: durOff };
}
function setDuration(a, kind, ticks) {
  a = Uint8Array.from(a);
  const view = dv(a), ver = a[8];
  const off = kind === 'tkhd' ? (ver ? 36 : 28) : (ver ? 32 : 24);
  if (!Number.isSafeInteger(ticks) || ticks < 0) BAD('invalid duration');
  if (ver) view.setBigUint64(off, BigInt(ticks));
  else if (ticks <= 0xffffffff) view.setUint32(off, ticks);
  else BAD('duration exceeds v0 ' + kind);
  return a;
}
function getEditStart(trak) {
  const edts = childMaybe(trak, 'edts');
  if (!edts) return 0;
  const elst = uniqueChild(edts, 'elst');
  const v = dv(elst), version = elst[8], count = v.getUint32(12);
  if (count !== 1 || version > 1) BAD('existing complex edit list not supported');
  const time = version === 1 ? Number(v.getBigInt64(24)) : v.getInt32(20);
  const rate = version === 1 ? v.getUint32(32) : v.getUint32(24);
  if (time < 0 || rate !== 0x00010000) BAD('existing edit list has unsupported offset or playback rate');
  return time;
}
function makeEdts(segments, scaleMovie, scaleMedia, mediaStart) {
  const entries = [u32(segments.length)];
  let total = 0;
  for (const s of segments) {
    const len = Math.round((s.to - s.from) * scaleMovie);
    const offset = Math.round(s.from * scaleMedia + mediaStart);
    if (len <= 0) BAD('empty edit entry');
    entries.push(u64(len), s64(offset), u16(1), u16(0));
    total += len;
  }
  const elst = makeBox('elst', join([Uint8Array.from([1,0,0,0]), ...entries]));
  return { edts: makeBox('edts', elst), duration: total };
}
function identify(data) {
  const tops = boxList(data);
  const moovs = tops.filter(b => b.type === 'moov');
  const mdats = tops.filter(b => b.type === 'mdat');
  if (moovs.length !== 1 || mdats.length !== 1 || tops.some(b => ['moof','mfra'].includes(b.type))) BAD('only one nonfragmented M4A audio track is supported');
  if (!tops.some(b => b.type === 'ftyp')) BAD('missing ftyp');
  const moovBox = moovs[0], mdatBox = mdats[0];
  const moov = Uint8Array.from(data.subarray(moovBox.start, moovBox.end));
  const tracks = boxList(moov, 8).filter(b => b.type === 'trak');
  if (tracks.length !== 1) BAD('only single-track audio supported');
  const trak = Uint8Array.from(moov.subarray(tracks[0].start, tracks[0].end));
  const mdia = uniqueChild(trak, 'mdia');
  const hdlr = uniqueChild(mdia, 'hdlr');
  if (String.fromCharCode(...hdlr.subarray(16,20)) !== 'soun') BAD('track is not audio');
  const mvhd = uniqueChild(moov, 'mvhd');
  const mdhd = uniqueChild(mdia, 'mdhd');
  const stbl = uniqueChild(uniqueChild(mdia, 'minf'), 'stbl');
  const stsd = uniqueChild(stbl, 'stsd');
  // Restrict experimental packet copying to AAC mp4a; edit-list method can use other sample descriptions.
  const sampleType = String.fromCharCode(...stsd.subarray(20,24));
  return { tops, moov, moovBox, mdatBox, trak, mvhd, mdhd, stbl, sampleType,
    movie: readDurationBox(mvhd,'mvhd'), media: readDurationBox(mdhd,'mdhd'),
    mediaStart: getEditStart(trak) };
}
function makeSegments(gaps, duration) {
  if (!Number.isFinite(duration) || duration <= 0) BAD('invalid recording duration');
  const sorted = gaps.filter(g => g && Number.isFinite(g.from) && Number.isFinite(g.to))
    .map(g => ({from:Math.max(0,Math.min(duration,g.from)),to:Math.max(0,Math.min(duration,g.to))}))
    .filter(g => g.to > g.from + 0.05).sort((a,b)=>a.from-b.from);
  const segments = []; let cursor = 0;
  for (const gap of sorted) {
    if (gap.to <= cursor) continue;
    if (gap.from > cursor) segments.push({from:cursor,to:gap.from});
    cursor = Math.max(cursor,gap.to);
  }
  if (duration - cursor > 0.001) segments.push({from:cursor,to:duration});
  if (!segments.length) BAD('all audio would be removed');
  return segments;
}
function mapSegments(segments) {
  let out = 0;
  return segments.map(s=>{ const value = {from:s.from,to:s.to, at:out}; out+=s.to-s.from; return value; });
}
function originalToCompiled(segments, t) {
  if (!segments.length || !Number.isFinite(t)) return 0;
  if (t <= segments[0].from) return 0;
  for (let i=0;i<segments.length;i++) {
    const s=segments[i], end=s.at+s.to-s.from;
    if (t < s.to) return Math.min(end,s.at+Math.max(0,t-s.from));
    if (i===segments.length-1) return end;
    if (t<segments[i+1].from) return end;
  }
  return 0;
}
function compiledToOriginal(segments,t) {
  if (!segments.length || !Number.isFinite(t)) return 0;
  for (const s of segments) if (t < s.at+s.to-s.from) return s.from+Math.max(0,t-s.at);
  return segments[segments.length-1].to;
}
function rewriteChunkOffsets(stbl, delta) {
  const offsets = childMaybe(stbl, 'stco') || childMaybe(stbl, 'co64');
  if (!offsets) BAD('no chunk offset table');
  const type = String.fromCharCode(...offsets.subarray(4,8));
  const v = dv(offsets), count=v.getUint32(12), step=type==='co64'?8:4;
  if (16+count*step>offsets.length) BAD('corrupt chunk offset table');
  for(let i=0;i<count;i++) {
    const at=16+i*step, old=step===8?Number(v.getBigUint64(at)):v.getUint32(at), dest=old+delta;
    if (dest<0||!Number.isSafeInteger(dest)) BAD('invalid relocated sample data offset');
    if(step===8)v.setBigUint64(at,BigInt(dest));else if(dest<=0xffffffff)v.setUint32(at,dest);else BAD('chunk offset exceeds 4GB');
  }
  return rebox(stbl,{[type]:offsets});
}
function rewriteTrack(trak, changes, duration) {
  const mdia = uniqueChild(trak,'mdia');
  const minf = uniqueChild(mdia,'minf');
  const originalStbl = uniqueChild(minf,'stbl');
  const stbl = changes.stbl || originalStbl;
  const newMinf = rebox(minf,{stbl});
  const newMdia = rebox(mdia,{minf:newMinf, ...(changes.mdhd?{mdhd:changes.mdhd}:{})});
  const tkhd = setDuration(uniqueChild(trak,'tkhd'),'tkhd',duration);
  return rebox(trak,{tkhd,mdia:newMdia, ...(changes.edts?{edts:changes.edts}:{})},changes.dropEdts?['edts']:[]);
}
function rebuildMoov(info, trak, newMovieDuration) {
  return rebox(info.moov,{mvhd:setDuration(info.mvhd,'mvhd',newMovieDuration),trak});
}
function createEditListAudio(original, gaps, originalDuration) {
  const data = original instanceof Uint8Array ? original : new Uint8Array(original);
  const info = identify(data);
  const duration = Math.min(originalDuration || info.movie.duration/info.movie.timescale, info.movie.duration/info.movie.timescale);
  const keep = makeSegments(gaps,duration);
  const edits = makeEdts(keep,info.movie.timescale,info.media.timescale,info.mediaStart);
  const tops = info.tops.filter(b=>b.type!=='moov');
  let mdatStart=0, at=0;
  for(const b of tops){if(b.type==='mdat') mdatStart=at;at+=b.end-b.start;}
  const delta=mdatStart-info.mdatBox.start;
  const origMdia=uniqueChild(info.trak,'mdia');
  const minf=uniqueChild(origMdia,'minf');
  const stbl=rewriteChunkOffsets(uniqueChild(minf,'stbl'),delta);
  const trak=rewriteTrack(info.trak,{stbl,edts:edits.edts},edits.duration);
  const moov=rebuildMoov(info,trak,edits.duration);
  return {blob:new Blob([...tops.map(b=>data.subarray(b.start,b.end)),moov],{type:'audio/mp4'}),
    segments:mapSegments(keep),total:edits.duration/info.movie.timescale,cuts:keep.length-1};
}
function parseStsz(stbl) {
  const s=uniqueChild(stbl,'stsz'),v=dv(s),fixed=v.getUint32(12),count=v.getUint32(16);
  if (count>5000000 || (!fixed && 20+count*4>s.length)) BAD('invalid stsz');
  return {count,size:i=>fixed||v.getUint32(20+i*4)};
}
function parseStsc(stbl) {
  const s=uniqueChild(stbl,'stsc'),v=dv(s),count=v.getUint32(12),entries=[];
  if(!count||16+count*12>s.length) BAD('bad stsc');
  for(let i=0;i<count;i++)entries.push({chunk:v.getUint32(16+i*12),samples:v.getUint32(20+i*12),id:v.getUint32(24+i*12)});
  if (entries[0].chunk!==1 || entries.some(e=>!e.chunk||!e.samples||e.id!==1)) BAD('unexpected stsc');
  return entries;
}
function parseStts(stbl,sampleCount) {
  const s=uniqueChild(stbl,'stts'),v=dv(s),count=v.getUint32(12),runs=[];let total=0;
  if(16+count*8>s.length) BAD('bad stts');
  for(let i=0;i<count;i++) {const n=v.getUint32(16+i*8),delta=v.getUint32(20+i*8); if(!n||!delta) BAD('bad stts run');runs.push({n,delta});total+=n;}
  if (total!==sampleCount) BAD('stts sample count mismatch');
  return runs;
}
function parseOffsets(stbl) {
  const b=childMaybe(stbl,'stco') || childMaybe(stbl,'co64');if(!b) BAD('no chunk offsets');
  const v=dv(b),count=v.getUint32(12),large=b[4]===99;
  if(16+count*(large?8:4)>b.length) BAD('bad offsets');
  return Array.from({length:count},(_,i)=>large?Number(v.getBigUint64(16+i*8)):v.getUint32(16+i*4));
}
function createPacketCopyAudio(original,gaps,originalDuration) {
  const data = original instanceof Uint8Array ? original : new Uint8Array(original);
  const info=identify(data);
  if(info.sampleType!=='mp4a') BAD('packet copy supports AAC mp4a only');
  const disallowed=['ctts','stss','sdtp','saio','saiz'];
  if(boxList(info.stbl,8).some(b=>disallowed.includes(b.type)))BAD('advanced sample tables not supported for packet copy');
  const duration=Math.min(originalDuration||info.movie.duration/info.movie.timescale,info.movie.duration/info.movie.timescale);
  const keep=makeSegments(gaps,duration);
  const sizes=parseStsz(info.stbl), sc=parseStsc(info.stbl), offsets=parseOffsets(info.stbl), runs=parseStts(info.stbl,sizes.count);
  let sample=0,run=0,runLeft=runs[0].n,time=0,chosen=[],chunks=[],currentRun=[],totalTicks=0;
  let block=0; // original chunks grouped by sample count (stsc)
  for(let i=0;i<offsets.length;i++) {
    while (block+1<sc.length && i+1>=sc[block+1].chunk)block++;
    let byte=offsets[i];
    for(let j=0;j<sc[block].samples && sample<sizes.count;j++) {
      const sz=sizes.size(sample),step=runs[run].delta;
      if(byte<info.mdatBox.start+info.mdatBox.header || byte+sz>info.mdatBox.end)BAD('sample outside mdat');
      const center=(time+step/2-info.mediaStart)/info.media.timescale;
      const keepIt=keep.some(k=>center>=k.from && center<k.to);
      if(keepIt) {
        chosen.push({byte,sz,delta:step,originalFrom:(time-info.mediaStart)/info.media.timescale});
        totalTicks+=step;
      }
      byte+=sz;time+=step;sample++;runLeft--;
      if(runLeft===0&&sample<sizes.count){run++;runLeft=runs[run].n;}
    }
  }
  if(sample!==sizes.count || !chosen.length)BAD('sample table incomplete or no retained samples');
  // A single MP4 chunk with concatenated AAC packets. Consecutive bytes remain zero-copy Blob slices.
  const spans=[];let begin=chosen[0].byte,end=begin+chosen[0].sz;
  for(let i=1;i<chosen.length;i++){
    const s=chosen[i];if(s.byte===end)end+=s.sz;
    else {spans.push({start:begin,end});begin=s.byte;end=s.byte+s.sz;}
  }
  spans.push({start:begin,end});
  let dataBytes=chosen.reduce((n,s)=>n+s.sz,0);
  if(dataBytes+8>0xffffffff)BAD('packet-copy output larger than 4GB');
  const ftypBox=info.tops.find(b=>b.type==='ftyp'),ftyp=data.subarray(ftypBox.start,ftypBox.end);
  const first=ftyp.length+8;
  const mediaDuration=totalTicks/info.media.timescale;
  const movieDuration=Math.round(mediaDuration*info.movie.timescale);
  const runRecords=[];
  for(const s of chosen) {const last=runRecords[runRecords.length-1];if(last&&last.delta===s.delta)last.n++;else runRecords.push({n:1,delta:s.delta});}
  const stts=makeBox('stts',join([new Uint8Array(4),u32(runRecords.length),...runRecords.flatMap(r=>[u32(r.n),u32(r.delta)])]));
  const sizePayload=new Uint8Array(12+chosen.length*4);
  const sizeView=new DataView(sizePayload.buffer);
  sizeView.setUint32(8,chosen.length);
  for(let i=0;i<chosen.length;i++)sizeView.setUint32(12+i*4,chosen[i].sz);
  const stsz=makeBox('stsz',sizePayload);
  const stsc=makeBox('stsc',join([new Uint8Array(4),u32(1),u32(1),u32(chosen.length),u32(1)]));
  const stco=makeBox('stco',join([new Uint8Array(4),u32(1),u32(first)]));
  const newStbl=rebox(info.stbl,{stts,stsz,stsc,stco},['co64','sgpd','sbgp']);
  const newMdhd=setDuration(info.mdhd,'mdhd',totalTicks);
  const trak=rewriteTrack(info.trak,{stbl:newStbl,mdhd:newMdhd,dropEdts:true},movieDuration);
  const moov=rebuildMoov(info,trak,movieDuration);
  // Match the output timeline to true packet boundaries rather than rounded Whisper gaps.
  // Coalesce adjacent retained samples without including discarded sample durations.
  const map=[];let at=0,from=chosen[0].originalFrom,to=from+chosen[0].delta/info.media.timescale;
  for(let i=1;i<chosen.length;i++){
    const s=chosen[i],next=s.originalFrom,nextEnd=next+s.delta/info.media.timescale;
    if(Math.abs(next-to)<0.00002)to=nextEnd;
    else {map.push({from:Math.max(0,from),to,at});at+=to-from;from=next;to=nextEnd;}
  }
  map.push({from:Math.max(0,from),to,at});
  const blob=new Blob([ftyp,u32(dataBytes+8),Uint8Array.from([109,100,97,116]),
    ...spans.map(r=>data.subarray(r.start,r.end)),moov],{type:'audio/mp4'});
  return {blob,segments:map,total:mediaDuration,cuts:map.length-1,samples:chosen.length};
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
    this.losslessMode = 'original';
    this.losslessResults = {};
    this.losslessUrls = {};
    this.pendingOriginalPosition = null;
    this.buildNonce = 0;
    this.audioFile = null;
    this.lastSourcePath = '';

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
    back.addEventListener('click', () => { this.seekOriginalTime(Math.max(0, this.originalTime() - 10)); });
    const forward = tools.createEl('button', { text: '+10s', attr: { 'aria-label': 'Forward ten seconds' } });
    forward.addEventListener('click', () => { this.seekOriginalTime(this.originalTime() + 10); });
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
    // Opt-in A/B experiment. Neither result is persisted, and normal playback stays the default.
    const ab = tools.createDiv({ cls: 'vap-ab', attr: { 'aria-label': 'Lossless M4A playback experiment' } });
    ab.createSpan({text: 'Lossless A/B 0.1.5'});
    this.abButton = ab.createEl('button', {text: 'Prepare both'});
    this.abButton.addEventListener('click', () => { void this.prepareLosslessAB(); });
    this.abSelect = ab.createEl('select', {attr: {'aria-label':'Playback engine'}});
    for (const [value,label] of [['original','Original (seeks)'],['edit','Edit list (A)'],['packet','Packet copy (B)']]) {
      this.abSelect.createEl('option',{value,text:label});
    }
    this.abSelect.addEventListener('change', () => this.selectLosslessMode(this.abSelect.value));
    this.abStatus = ab.createDiv({cls:'vap-ab-status',text:'Original playback. Tap Prepare both to test lossless edits.'});
    this.info = tools.createSpan({ cls: 'vap-info' });
    this.scrollEl = root.createDiv({ cls: 'vap-scroll' });
    this.audio.addEventListener('timeupdate', () => {
      this.skipPauseIfNeeded();
      this.updateHighlight();
      if (this.originalTime() - this.lastSavedAt >= 15) this.storeResume();
    });
    this.audio.addEventListener('seeked', () => { this.updateHighlight(); });
    this.audio.addEventListener('pause', () => { void this.storeResume(); });
    this.audio.addEventListener('ended', () => { void this.storeResume(true); });
    this.audio.addEventListener('loadedmetadata', () => {
      const saved = this.pendingOriginalPosition === null
        ? (this.plugin.data.positions?.[this.notePath] || 0) : this.pendingOriginalPosition;
      this.pendingOriginalPosition = null;
      const playtime = this.toPlaybackTime(saved);
      if (playtime > 0 && Number.isFinite(this.audio.duration) && playtime < this.audio.duration - 0.1) this.audio.currentTime = playtime;
      this.updateHighlight();
    });
    this.audio.addEventListener('error', () => {
      if (this.losslessMode === 'original') return;
      new Notice('iOS rejected the test M4A; restored original playback.');
      this.selectLosslessMode('original');
    });
    if (this.notePath) await this.loadRecording(this.notePath);
  }

  originalTime() {
    if (!this.audio) return 0;
    if (this.losslessMode === 'original') return this.audio.currentTime || 0;
    return compiledToOriginal(this.losslessResults[this.losslessMode]?.segments || [], this.audio.currentTime);
  }
  toPlaybackTime(original) {
    if (this.losslessMode === 'original') return original;
    return originalToCompiled(this.losslessResults[this.losslessMode]?.segments || [], original);
  }
  seekOriginalTime(original) {
    if (!this.audio) return;
    const t=this.toPlaybackTime(Math.max(0, original));
    this.audio.currentTime = Math.min(Number.isFinite(this.audio.duration) ? Math.max(0,this.audio.duration-0.05):t,t);
  }
  releaseLossless() {
    ++this.buildNonce;
    for(const url of Object.values(this.losslessUrls)) URL.revokeObjectURL(url);
    this.losslessUrls={}; this.losslessResults={};
    this.losslessMode='original';
    if(this.abSelect) this.abSelect.value='original';
  }
  async prepareLosslessAB() {
    if (!this.audioFile || !this.pauseRanges.length || !this.audio || this.abButton.disabled) {
      if (!this.pauseRanges.length) new Notice('No long speech gaps found in this recording.');
      return;
    }
    if (this.losslessResults.edit && this.losslessResults.packet) {
      this.abStatus.setText('Both results ready. Choose A or B to listen; original remains available.');
      return;
    }
    const nonce=++this.buildNonce, source=this.currentAudio;
    this.abButton.disabled=true;
    let original;
    try {
      const len=this.audioFile.stat?.size||0;
      if (len>128*1024*1024) throw new Error('Source exceeds the 128 MB test limit; original playback remains available.');
      if (!Number.isFinite(this.audio.duration) || this.audio.duration<=0) throw new Error('Wait for recording metadata, then retry.');
      const duration=this.losslessMode==='original' ? this.audio.duration : this.originalDuration;
      if(!Number.isFinite(duration)||duration<=0)throw new Error('Unknown original duration');
      this.originalDuration=duration;
      this.abStatus.setText('Reading original M4A into temporary memory…');
      original=new Uint8Array(await this.app.vault.readBinary(this.audioFile));
      if(nonce!==this.buildNonce||source!==this.currentAudio)return;
      for(const [mode,label,fn] of [['edit','Edit list',createEditListAudio],['packet','Packet copy',createPacketCopyAudio]]) {
        this.abStatus.setText('Building '+label+' M4A…');
        // Yield to render the status before the synchronous byte-level assembly.
        await new Promise(resolve=>setTimeout(resolve,15));
        if(nonce!==this.buildNonce||source!==this.currentAudio)return;
        const start=performance.now(),result=fn(original,this.pauseRanges,duration);
        result.elapsed=Math.round(performance.now()-start);
        this.losslessResults[mode]=result;
      }
      if(nonce!==this.buildNonce||source!==this.currentAudio)return;
      const a=this.losslessResults.edit,b=this.losslessResults.packet;
      this.abStatus.setText('Ready · A '+a.elapsed+' ms / '+(a.blob.size/1024/1024).toFixed(1)+' MB · B '+b.elapsed+' ms / '+(b.blob.size/1024/1024).toFixed(1)+' MB · '+a.cuts+' pauses');
      new Notice('Both M4A variants ready. Choose A or B.');
    }catch(e){
      if(nonce===this.buildNonce){
        this.abStatus.setText('Could not build A/B: '+(e?.message||e));
        new Notice('Lossless M4A test: '+(e?.message||e));
      }
    }finally{this.abButton.disabled=false;original=null;}
  }
  selectLosslessMode(mode) {
    if (!['original','edit','packet'].includes(mode))return;
    if (mode!=='original' && !this.losslessResults[mode]) {
      this.abSelect.value=this.losslessMode;
      new Notice('Tap Prepare both before selecting this playback mode.');return;
    }
    if(mode===this.losslessMode)return;
    const position=this.originalTime();
    this.audio.pause();
    this.pendingOriginalPosition=position;
    this.losslessMode=mode;
    this.abSelect.value=mode;
    if(mode!=='original'&&!this.losslessUrls[mode])this.losslessUrls[mode]=URL.createObjectURL(this.losslessResults[mode].blob);
    const url=mode==='original'?this.app.vault.getResourcePath(this.audioFile):this.losslessUrls[mode];
    this.audio.src=url;
    this.audio.load();
    this.abStatus.setText(mode==='original'?'Original media + live seeks.':
      (mode==='edit'?'A: original AAC with playback edit list.':'B: copied AAC packets, new M4A.')+' Press Play; no automatic pause seeks.');
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
        this.audio.pause();
        this.releaseLossless();
        this.currentAudio = audioFile.path;
        this.audioFile = audioFile;
        this.originalDuration = null;
        this.abStatus.setText('Original playback. Tap Prepare both to test lossless edits.');
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
          const seek = () => { this.seekOriginalTime(anchor.start); void this.audio.play().catch(() => {}); };
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
    if (!this.audio || this.losslessMode !== 'original' || this.audio.paused || this.audio.seeking ||
        this.plugin.data.skipPauses === false || !this.pauseRanges.length) return;
    const target = pauseSkipDestination(this.pauseRanges, this.audio.currentTime);
    if (target !== null && (!Number.isFinite(this.audio.duration) || target < this.audio.duration)) {
      this.audio.currentTime = target;
    }
  }

  updateHighlight() {
    if (!this.audio || !this.lines.length) return;
    const selected = highlightedLineIndex(this.lines, this.originalTime());
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
    this.lastSavedAt = this.originalTime();
    this.plugin.data.positions[this.notePath] = ended ? 0 : this.lastSavedAt;
    await this.plugin.saveData(this.plugin.data);
  }
  async onClose() {
    if (this.audio) { this.audio.pause(); await this.storeResume(); }
    this.releaseLossless();
  }
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

class InnermazeSettingsTab extends PluginSettingTab {
  constructor(app, plugin) { super(app, plugin); this.plugin = plugin; }
  display() {
    this.containerEl.empty();
    new Setting(this.containerEl)
      .setName('Open player automatically')
      .setDesc('Open the Innermaze player when you open a Voice Archive recording note. On desktop it appears in the right sidebar; regular notes are unaffected.')
      .addToggle(toggle => toggle
        .setValue(this.plugin.data.autoOpenPlayer !== false)
        .onChange(async enabled => {
          this.plugin.data.autoOpenPlayer = enabled;
          await this.plugin.saveData(this.plugin.data);
          // Enabling should work immediately with the current recording.
          this.plugin.queueAutoOpen(this.app.workspace.getActiveFile());
        }));
  }
}

module.exports = class InnermazePlugin extends Plugin {
  async onload() {
    this.data = Object.assign({ positions: {}, skipPauses: true, autoOpenPlayer: true }, await this.loadData() || {});
    if (!this.data.positions) this.data.positions = {};
    this.autoOpenRequest = 0;
    this.lastRecordingPath = "";
    this.autoOpenTask = Promise.resolve();
    this.registerView(VIEW_TYPE, leaf => new InnermazeView(leaf, this));
    this.addSettingTab(new InnermazeSettingsTab(this.app, this));
    this.registerEvent(this.app.workspace.on('file-open', file => this.queueAutoOpen(file)));
    this.app.workspace.onLayoutReady(() => this.queueAutoOpen(this.app.workspace.getActiveFile()));
    this.addCommand({
      id: 'open-innermaze-player',
      name: 'Open player for current voice recording',
      callback: async () => {
        // Once a player tab is active, getActiveFile() may be null. Use the
        // last verified recording, not an arbitrary document or player title.
        const active = this.app.workspace.getActiveFile();
        const remembered = this.lastRecordingPath ||
          this.app.workspace.getLeavesOfType(VIEW_TYPE)[0]?.view?.notePath || '';
        const note = active instanceof TFile && active.extension === 'md'
          ? active : this.app.vault.getAbstractFileByPath(remembered);
        if (!(note instanceof TFile) || note.extension !== 'md') {
          new Notice('Open a Voice Archive Markdown note first.'); return;
        }
        try {
          const source = await this.app.vault.cachedRead(note);
          if (!isRecordingNote(note, source)) {
            new Notice('Current note is not an Innermaze recording.'); return;
          }
          await this.openPlayerForNote(note);
        } catch (error) { new Notice(`Innermaze player: ${error.message || error}`); }
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
  // Obsidian emits file-open(null) when a custom player tab becomes active.
  // Ignore that synthetic event: cancelling in-flight player creation here
  // left users with no player, or an empty pane. Only real Markdown navigation
  // can supersede a pending recording request.
  queueAutoOpen(file) {
    if (file == null) return this.autoOpenTask;
    if (!(file instanceof TFile) || file.extension !== 'md') return this.autoOpenTask;
    const token = ++this.autoOpenRequest;
    this.autoOpenTask = this.autoOpenTask.catch(() => {}).then(async () => {
      if (this.data.autoOpenPlayer === false || token !== this.autoOpenRequest) return;
      // Avoid reading every unrelated Markdown note in a large vault.
      const inArchive = file.path.startsWith(`${NOTE_DIR}/`);
      const cached = this.app.metadataCache?.getFileCache(file)?.frontmatter;
      if (!inArchive && cached?.voice_archive !== true) return;
      const source = await this.app.vault.cachedRead(file);
      if (token !== this.autoOpenRequest || !isRecordingNote(file, source)) return;
      await this.openPlayerForNote(file, token);
    }).catch(error => { console.error('Innermaze auto-open:', error); });
    return this.autoOpenTask;
  }

  async openPlayerForNote(file, token = null) {
    const workspace = this.app.workspace;
    // Match the original 0.1.1 manually opened player: an ordinary Obsidian
    // tab, with the complete audio controls and synchronized transcript.
    // Sidebar APIs are not consistently available and were never needed.
    let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) leaf = workspace.getLeaf('tab');
    if (!leaf) throw new Error('Obsidian could not create a player tab.');
    if (leaf.getViewState().type !== VIEW_TYPE) {
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    if (token !== null && token !== this.autoOpenRequest) return;
    // Obsidian 1.7.2+ restores background tabs as DeferredView instances.
    // A leaf can report our view type before its InnermazeView is constructed.
    // Reveal and await hydration BEFORE touching the custom view's methods.
    await workspace.revealLeaf(leaf);
    if (leaf.isDeferred && typeof leaf.loadIfDeferred === 'function') {
      await leaf.loadIfDeferred();
    }
    if (token !== null && token !== this.autoOpenRequest) return;
    if (!(leaf.view instanceof InnermazeView)) {
      throw new Error('Obsidian could not initialize the Innermaze player view.');
    }
    await leaf.view.setRecordingPath(file.path);
    if (token !== null && token !== this.autoOpenRequest) return;
    this.lastRecordingPath = file.path;
  }

  async onunload() { /* registered views/listeners are disposed by Obsidian */ }
};

// Exposed exclusively for node-level test harness, not needed by Obsidian.
if (typeof process !== 'undefined' && process.env && process.env.INNERMAZE_TEST === '1') {
  module.exports._internals = { parseNote, alignParagraphs, similarity, formatTime,
    makeGenericNote, isAudioName, isRecordingNote, pauseSkipRanges, pauseSkipDestination,
    highlightedLineIndex, createEditListAudio,createPacketCopyAudio,makeSegments,originalToCompiled,compiledToOriginal };
}
