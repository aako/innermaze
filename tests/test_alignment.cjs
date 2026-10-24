// Offline Node test: mocks Obsidian API; verifies alignment without a vault.
const Module = require('module');
const originalLoad = Module._load;
Module._load = (name, parent, isMain) => name === 'obsidian'
  ? { Plugin: class {}, ItemView: class {}, Notice: class {}, TFile: class {}, Modal: class {} }
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
