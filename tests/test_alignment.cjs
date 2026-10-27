// Offline Node test: mocks Obsidian API; verifies alignment without a vault.
const Module = require('module');
const originalLoad = Module._load;
Module._load = (name, parent, isMain) => name === 'obsidian'
  ? { Plugin: class {}, PluginSettingTab: class {}, Setting: class {}, ItemView: class {}, FileView: class {}, Notice: class {}, TFile: class {}, Modal: class {}, MarkdownView: class {}, Platform: { isMobile: false } }
  : originalLoad(name, parent, isMain);
process.env.INNERMAZE_TEST = '1';
const assert = require('node:assert/strict');
const { _internals } = require('../main.js');
const blocks = [
  { text: 'Hello world here.' },
  { text: 'This is test two.' },
  { text: 'A third sentence.' }
];
const cases = [
  [['Hello world there.', 'This is test two.', 'A third sentence.'], [0, 1, 2]],
  [['Hello world here.', 'An unrelated note.', 'This is test two.', 'A third sentence.'], [0, -1, 1, 2]],
  [['Hello world here.', 'A third sentence.'], [0, 2]],
  [['This is test two.', 'Hello world here.', 'A third sentence.'], [1, 0, 2]]
];
for (const [input, expected] of cases) {
  assert.deepEqual(_internals.alignParagraphs(input, blocks), expected);
  console.log('PASS:', input.length, 'paragraph alignment', expected);
}
const parsed = _internals.parseNote('---\nvoice_audio: "Audio/test.m4a"\nvoice_sync: "Sync/test.json"\nrecording_id: "vm-123"\n---\n\nHello world.\n\nAnother line.\n');
assert.equal(parsed.paragraphs.length, 2);
console.log('PASS: plain Markdown parsing');

const id = 'audio-abcdef12345';
const generic = _internals.makeGenericNote(id, 'Hello.m4a', 'Voice Archive/Audio/a.m4a', 'abcdef12345');
const p2 = _internals.parseNote(generic);
assert.equal(p2.fields.voice_audio, 'Voice Archive/Audio/a.m4a');
assert.equal(p2.paragraphs.length, 0);
assert.ok(generic.includes('transcription_status: "pending"'));
console.log('PASS: mobile import creates timestamp-free pending Markdown');

// Gap-skipping is nondestructive and anchored to actual Whisper word timings.
const { pauseSkipRanges, pauseSkipDestination, highlightedLineIndex } = _internals;
const utterance = [
  { start: 0.3, end: 0.9, text: ' I' },
  { start: 0.92, end: 1.5, text: ' remember' },
  { start: 8.0, end: 8.5, text: ' yesterday' },
  { start: 8.55, end: 9.0, text: ' clearly' },
  { start: 64.0, end: 64.2, text: ' Yes.' }
];
const gaps = pauseSkipRanges(utterance);
assert.equal(gaps.length, 2);
assert.ok(Math.abs(gaps[0].from - 1.9) < 1e-6);
assert.ok(Math.abs(gaps[0].to - 7.6) < 1e-6);
assert.equal(pauseSkipDestination(gaps, 1.7), null, 'retain padding after a word');
assert.equal(pauseSkipDestination(gaps, 2.1), 7.6, 'jump only inside long pause');
assert.equal(pauseSkipDestination(gaps, 7.9), null, 'retain padding before next word');
assert.equal(pauseSkipDestination(gaps, 8.8), null, 'do not skip natural short pauses');
assert.ok(pauseSkipDestination(gaps, 12) > 63.0, 'long ADHD pause is skipped');
assert.equal(pauseSkipDestination(gaps, Number.NaN), null);
assert.equal(pauseSkipRanges([{start: 2, end: 4, text: ' hi'}, {start: 3, end: 5, text: ' there'}]).length, 0);
assert.equal(pauseSkipRanges([{start: 1, end: 2, text: ' x'}, {start: 3.5, end: 4.0, text: ' y'}]).length, 0, 'preserve short pauses');
assert.equal(pauseSkipRanges([]).length, 0);
assert.equal(pauseSkipRanges([{start: -10, end: 1, text: ' corrupted'}]).length, 0);
console.log('PASS: skip only long word-timed pauses, with speech padding');

const highlighted = [
  { anchor: { start: 1, end: 2 } },
  { anchor: null },
  { anchor: { start: 9, end: 10 } },
  { anchor: { start: 70, end: 71 } }
];
assert.equal(highlightedLineIndex(highlighted, 0), 0, 'highlight first line before speech');
assert.equal(highlightedLineIndex(highlighted, 1.2), 0);
assert.equal(highlightedLineIndex(highlighted, 7), 0, 'retain previous highlight in long gap');
assert.equal(highlightedLineIndex(highlighted, 9.2), 2);
assert.equal(highlightedLineIndex(highlighted, 50), 2, 'highlight never disappears between lines');
assert.equal(highlightedLineIndex(highlighted, 200), 3, 'highlight last after audio ends');
assert.equal(highlightedLineIndex([{anchor:null}], 1), -1, 'never invent timing for unaligned lines');
console.log('PASS: uninterrupted line highlight across natural and skipped pauses');
