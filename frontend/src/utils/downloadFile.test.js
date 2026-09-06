import assert from 'node:assert/strict';
import test from 'node:test';
import { downloadBlob, filenameFromContentDisposition } from './downloadFile.js';

test('filenameFromContentDisposition prefers quoted filename', () => {
  assert.equal(
    filenameFromContentDisposition('attachment; filename="piece.musicxml"', 'fallback.musicxml'),
    'piece.musicxml',
  );
});

test('filenameFromContentDisposition falls back when header missing', () => {
  assert.equal(filenameFromContentDisposition(null, 'composition.mid'), 'composition.mid');
});

test('downloadBlob creates, clicks, and revokes object URL', () => {
  const revoked = [];
  const clicks = [];
  const removed = [];
  const fakeAnchor = {
    href: '',
    download: '',
    rel: '',
    click() {
      clicks.push(this.download);
    },
    remove() {
      removed.push(true);
    },
  };
  const fakeDocument = {
    createElement() {
      return fakeAnchor;
    },
    body: {
      appendChild() {},
    },
  };
  const blob = { size: 12, type: 'audio/midi' };

  downloadBlob(blob, 'song.mid', {
    createObjectURL: () => 'blob:mock-url',
    revokeObjectURL: (url) => revoked.push(url),
    documentRef: fakeDocument,
  });

  assert.equal(fakeAnchor.href, 'blob:mock-url');
  assert.equal(fakeAnchor.download, 'song.mid');
  assert.deepEqual(clicks, ['song.mid']);
  assert.deepEqual(revoked, ['blob:mock-url']);
  assert.deepEqual(removed, [true]);
});
