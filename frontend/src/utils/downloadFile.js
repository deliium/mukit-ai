/**
 * Trigger a browser file download from a Blob and revoke the object URL.
 */
export function downloadBlob(blob, filename, { createObjectURL, revokeObjectURL, documentRef } = {}) {
  const createUrl = createObjectURL || (typeof URL !== 'undefined' ? URL.createObjectURL.bind(URL) : null);
  const revokeUrl = revokeObjectURL || (typeof URL !== 'undefined' ? URL.revokeObjectURL.bind(URL) : null);
  const doc = documentRef || (typeof document !== 'undefined' ? document : null);

  if (!createUrl || !revokeUrl || !doc) {
    throw new Error('Browser download APIs are unavailable');
  }

  console.debug('[downloadFile] Download started', {
    filename,
    blobSize: blob?.size,
    blobType: blob?.type,
  });

  const objectUrl = createUrl(blob);
  try {
    const anchor = doc.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.rel = 'noopener';
    doc.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    console.debug('[downloadFile] Download click dispatched', { filename });
  } finally {
    revokeUrl(objectUrl);
    console.debug('[downloadFile] Object URL revoked', { filename });
  }
}

export function filenameFromContentDisposition(headerValue, fallback) {
  if (!headerValue || typeof headerValue !== 'string') {
    return fallback;
  }
  const utfMatch = /filename\*=UTF-8''([^;]+)/i.exec(headerValue);
  if (utfMatch?.[1]) {
    try {
      return decodeURIComponent(utfMatch[1].trim());
    } catch {
      return utfMatch[1].trim();
    }
  }
  const plainMatch = /filename="?([^";]+)"?/i.exec(headerValue);
  if (plainMatch?.[1]) {
    return plainMatch[1].trim();
  }
  return fallback;
}
