/**
 * Session callback so the Picture tab can pause Tone without resetting the cursor.
 * PlaybackControls registers the implementation. No browser globals here.
 */

let releaseTone = () => {};

export function registerPictureToneRelease(fn) {
  releaseTone = typeof fn === 'function' ? fn : () => {};
}

export function releaseToneForPicture() {
  releaseTone();
}
