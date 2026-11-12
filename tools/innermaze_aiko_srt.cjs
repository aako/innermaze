// filename: innermaze_aiko_srt.cjs
// version: 0.1.0
// timestamp: 2026-11-12 14:40:00
'use strict';
// Dependency-free helpers. Embedded into Innermaze main.js; exported only for tests.
function innermazeMobileDisplayTitle(filename) {
  const input = String(filename || '').split(/[\\/]/).pop().normalize('NFC');
  const stem = input.replace(/\.(?:m4a|mp4|m4b|mp3|wav|aif|aiff|caf|flac|ogg|opus|aac|webm|wma|amr)$/i,'');
  const title = stem.replace(/[\x00-\x1f\x7f]/g,' ').trim();
  return title || 'Untitled Audio';
}
function innermazeMobileUniqueNotePath(vault, filename, recordingId) {
  if (!/^(?:vm-[A-Fa-f0-9-]{36}|audio-[a-f0-9]{64})$/.test(recordingId))
    throw new Error('Unexpected immutable mobile recording ID');
  let stem = innermazeMobileDisplayTitle(filename)
    .replace(/[\\/:*?"<>|]/g,'-').replace(/\s+/g,' ').replace(/[. ]+$/g,'').trim();
  if (!stem || stem === '.' || stem === '..') stem = 'Untitled Audio';
  stem = [...stem].slice(0,120).join('').replace(/[. ]+$/g,'') || 'Untitled Audio';
  const exists = path => {
    const key = path.normalize('NFC').toLocaleLowerCase();
    const all = vault.getFiles().map(file=>file.path);
    if (typeof vault.getAllLoadedFiles === 'function')
      all.push(...vault.getAllLoadedFiles().map(file=>file.path));
    return Boolean(vault.getAbstractFileByPath(path)) ||
      all.some(value=>value.normalize('NFC').toLocaleLowerCase()===key);
  };
  let path = `${NOTE_DIR}/${stem}.md`;
  if (!exists(path)) return path;
  const suffix = recordingId.slice(-8);
  for (let i=1;i<=100;i++) {
    path = `${NOTE_DIR}/${stem} (${suffix}${i===1?'':`-${i}`}).md`;
    if (!exists(path)) return path;
  }
  throw new Error('Recording filename collisions exhausted; no note created');
}
function innermazeAikoTime(input) {
  const m = String(input).trim().match(/^(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})$/);
  if (!m || Number(m[2])>59 || Number(m[3])>59) throw new Error('Invalid SRT timestamp: '+String(input));
  return Number(m[1])*3600+Number(m[2])*60+Number(m[3])+Number(m[4])/1000;
}
function innermazeParseAikoSrt(input, duration) {
  if (!Number.isFinite(duration) || duration<=0) throw new Error('Recording duration unavailable; download audio before importing SRT');
  if (typeof input!=='string' || input.length>4*1024*1024) throw new Error('SRT missing or exceeds 4 MiB');
  const chunks = input.replace(/^\uFEFF/,'').replace(/\r\n?/g,'\n').trim().split(/\n\s*\n+/);
  if (chunks.length>10000) throw new Error('SRT has too many captions');
  const blocks=[];
  for (const part of chunks) {
    const rows=part.split('\n').map(s=>s.trim());
    if (rows.length===0 || !rows[0]) continue;
    if (/^\d+$/.test(rows[0])) rows.shift();
    const times=rows.shift() || '';
    const m=times.match(/^(\d{1,3}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{1,3}:\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?$/);
    if (!m) throw new Error('Malformed Aiko SRT timestamp line: '+times.slice(0,80));
    const start=innermazeAikoTime(m[1]), end=innermazeAikoTime(m[2]);
    const caption=blocks.length+1;
    if (!(end>start))
      throw new Error(`Aiko SRT caption ${caption} has an invalid interval (${start.toFixed(3)}–${end.toFixed(3)}s). No transcript changed.`);
    if (blocks.length && start<blocks[blocks.length-1].start)
      throw new Error('SRT captions are not in chronological order');
    // Whisper subtitle endpoints can extend slightly beyond media metadata.
    // Clamp only the tail, never move a caption's start or invent new timing.
    // Larger mismatches may indicate the wrong SRT/recording: stop for review.
    const overrun=end-duration;
    if (start>=duration || overrun>2.0+0.000001)
      throw new Error(`Aiko SRT caption ${caption} ends at ${end.toFixed(3)}s (starts ${start.toFixed(3)}s), but audio duration is ${duration.toFixed(3)}s; overrun ${Math.max(0,overrun).toFixed(3)}s. Check that this SRT belongs to this recording. No transcript changed.`);
    const playableEnd=Math.min(end,duration);
    // Aiko's SRT may include visual emphasis and line breaks; player displays plain text.
    const text=rows.join(' ').replace(/<[^>]*>/g,'').replace(/\{\\an\d+\}/g,'')
      .replace(/\s+/g,' ').trim();
    if (!text) throw new Error('SRT has an empty caption');
    blocks.push({start,end:playableEnd,text});
  }
  if (!blocks.length) throw new Error('SRT contains no caption intervals');
  return blocks;
}
function innermazeAikoSidecar(recordingId, blocks, duration, srtName) {
  if (!recordingId || !Array.isArray(blocks) || !blocks.length) throw new Error('Invalid Aiko transcript identity');
  return {
    format:'voice-archive-sync-v1', recording_id:recordingId,
    source:'aiko', source_format:'srt', source_filename:String(srtName),
    timing_granularity:'segment', duration_seconds:duration,
    blocks:blocks.map((b,i)=>({id:`${recordingId}-${String(i).padStart(5,'0')}`,
      start:b.start,end:b.end,text:b.text})),
    words:[] // SRT carries segment timing, NOT word-level timings.
  };
}
function innermazeAikoUpdateNote(raw, paragraphs) {
  const match=String(raw).match(/^(---\r?\n)([\s\S]*?)(\r?\n---\r?\n?)/);
  if (!match || !/^voice_archive:\s*true\s*$/m.test(match[2]))
    throw new Error('Selected note has no verified Voice Archive frontmatter');
  let header=match[2];
  if (!/^transcription_status:\s*['"]?(pending|ready)['"]?\s*$/m.test(header))
    throw new Error('Unknown transcription status; refusing to rewrite note');
  if (/^transcription_source:\s*/m.test(header))
    header=header.replace(/^transcription_source:\s*.*$/m,'transcription_source: "aiko"');
  else header+='\ntranscription_source: "aiko"';
  header=header.replace(/^transcription_status:\s*['"]?(pending|ready)['"]?\s*$/m,
    'transcription_status: "ready"');
  return match[1]+header+match[3]+'\n'+paragraphs.map(x=>x.text).join('\n\n')+'\n';
}
if (typeof module!=='undefined' && module.exports && typeof process!=='undefined' && process.env.INNERMAZE_TEST==='1') {
  module.exports={innermazeMobileDisplayTitle,innermazeMobileUniqueNotePath,
    innermazeAikoTime,innermazeParseAikoSrt,innermazeAikoSidecar,innermazeAikoUpdateNote};
}
