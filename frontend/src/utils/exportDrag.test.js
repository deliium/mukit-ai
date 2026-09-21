import assert from 'node:assert/strict';
import test from 'node:test';
import {
  attachExportFileToDataTransfer,
  blobToExportFile,
  createExportDragStartHandler,
  resolveExportDragMime,
} from './exportDrag.js';

test('blobToExportFile builds a File with MIDI mime and filename', () => {
  const blob = new Blob([Uint8Array.from([0x4d, 0x54, 0x68, 0x64])], { type: 'audio/midi' });
  const file = blobToExportFile(blob, 'song-export.mid', 'audio/midi');
  assert.equal(file.name, 'song-export.mid');
  assert.equal(file.type, 'audio/midi');
  assert.equal(file.size, 4);
});

test('blobToExportFile rejects missing filename', () => {
  assert.throws(() => blobToExportFile(new Blob(['x']), '  '), /filename/);
});

test('resolveExportDragMime prefers audio/midi for midi format', () => {
  assert.equal(resolveExportDragMime('midi', 'audio/midi; charset=binary'), 'audio/midi');
  assert.equal(resolveExportDragMime('midi', 'application/octet-stream'), 'audio/midi');
  assert.equal(resolveExportDragMime('musicxml', ''), 'application/vnd.recordare.musicxml+xml');
});

test('attachExportFileToDataTransfer adds File via items.add', () => {
  const added = [];
  const dataTransfer = {
    effectAllowed: 'none',
    items: {
      add(file) {
        added.push(file);
      },
    },
  };
  const file = blobToExportFile(new Blob(['abc']), 'clip.mid', 'audio/midi');
  assert.equal(attachExportFileToDataTransfer(dataTransfer, file), true);
  assert.equal(dataTransfer.effectAllowed, 'copy');
  assert.equal(added.length, 1);
  assert.equal(added[0].name, 'clip.mid');
});

test('createExportDragStartHandler prevents default when file missing', () => {
  let prevented = false;
  const handler = createExportDragStartHandler(() => null, { format: 'midi' });
  handler({
    preventDefault() {
      prevented = true;
    },
    dataTransfer: { items: { add() {} } },
  });
  assert.equal(prevented, true);
});

test('createExportDragStartHandler attaches ready file', () => {
  const file = blobToExportFile(new Blob(['mid']), 'ready.mid', 'audio/midi');
  const added = [];
  let prevented = false;
  const handler = createExportDragStartHandler(() => file, { format: 'midi' });
  handler({
    preventDefault() {
      prevented = true;
    },
    dataTransfer: {
      effectAllowed: 'none',
      items: {
        add(item) {
          added.push(item);
        },
      },
    },
  });
  assert.equal(prevented, false);
  assert.equal(added[0], file);
});
