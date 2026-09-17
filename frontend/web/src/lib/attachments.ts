export const MESSAGE_TEXT_MAX_CHARS = 32_000;
export const MESSAGE_ATTACHMENT_MAX_COUNT = 5;
export const MESSAGE_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
export const MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES = 25 * 1024 * 1024;
export const MESSAGE_ATTACHMENT_ACCEPT = [
  '.png',
  '.jpg',
  '.jpeg',
  '.webp',
  '.pdf',
  '.md',
  '.markdown',
  '.txt',
  '.json',
  '.csv',
].join(',');

const ATTACHMENT_MIME_BY_SUFFIX: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.pdf': 'application/pdf',
  '.md': 'text/markdown',
  '.markdown': 'text/markdown',
  '.txt': 'text/plain',
  '.json': 'application/json',
  '.csv': 'text/csv',
};

export type ComposerAttachmentIssue =
  | { code: 'unsupported'; fileName: string }
  | { code: 'too-large'; fileName: string; limitBytes: number }
  | { code: 'too-many'; limitCount: number }
  | { code: 'too-large-total'; limitBytes: number };

export function attachmentSuffix(name: string): string {
  const trimmed = String(name || '').trim().toLowerCase();
  const dot = trimmed.lastIndexOf('.');
  return dot >= 0 ? trimmed.slice(dot) : '';
}

export function attachmentMime(file: Pick<File, 'name' | 'type'>): string {
  const suffix = attachmentSuffix(file.name);
  return ATTACHMENT_MIME_BY_SUFFIX[suffix]
    || String(file.type || '').split(';', 1)[0].trim()
    || 'application/octet-stream';
}

export function isSupportedAttachment(file: Pick<File, 'name' | 'type'>): boolean {
  return Object.hasOwn(ATTACHMENT_MIME_BY_SUFFIX, attachmentSuffix(file.name));
}

export function isImageAttachment(file: Pick<File, 'name' | 'type'>): boolean {
  return attachmentMime(file).startsWith('image/');
}

function attachmentKey(file: Pick<File, 'name' | 'size' | 'type'> & { lastModified?: number }): string {
  return [
    file.name,
    String(file.size),
    attachmentMime(file),
    String(file.lastModified ?? ''),
  ].join('::');
}

export function addComposerFiles(
  existing: Array<Pick<File, 'name' | 'size' | 'type'> & { lastModified?: number }>,
  incoming: File[],
): { accepted: File[]; issues: ComposerAttachmentIssue[] } {
  const accepted: File[] = [];
  const issues: ComposerAttachmentIssue[] = [];
  const seen = new Set(existing.map(attachmentKey));
  let totalBytes = existing.reduce((sum, file) => sum + Math.max(0, file.size || 0), 0);
  let count = existing.length;

  for (const file of incoming) {
    const key = attachmentKey(file);
    if (seen.has(key)) continue;
    seen.add(key);
    if (!isSupportedAttachment(file)) {
      issues.push({ code: 'unsupported', fileName: file.name });
      continue;
    }
    if (count >= MESSAGE_ATTACHMENT_MAX_COUNT) {
      issues.push({ code: 'too-many', limitCount: MESSAGE_ATTACHMENT_MAX_COUNT });
      continue;
    }
    if (file.size > MESSAGE_ATTACHMENT_MAX_BYTES) {
      issues.push({
        code: 'too-large',
        fileName: file.name,
        limitBytes: MESSAGE_ATTACHMENT_MAX_BYTES,
      });
      continue;
    }
    if (totalBytes + file.size > MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES) {
      issues.push({
        code: 'too-large-total',
        limitBytes: MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES,
      });
      continue;
    }
    accepted.push(file);
    totalBytes += file.size;
    count += 1;
  }
  return { accepted, issues };
}

type DataTransferFileLike = {
  files?: Iterable<File> | ArrayLike<File> | null;
  items?: Iterable<{ kind?: string; getAsFile?: () => File | null }> | ArrayLike<{ kind?: string; getAsFile?: () => File | null }> | null;
  types?: Iterable<string> | ArrayLike<string> | null;
};

function iterableToArray<T>(value: Iterable<T> | ArrayLike<T> | null | undefined): T[] {
  if (!value) return [];
  return Array.from(value as ArrayLike<T>);
}

export function dataTransferHasFiles(data: DataTransferFileLike | null | undefined): boolean {
  const types = iterableToArray(data?.types).map((value) => String(value));
  return types.includes('Files') || extractFilesFromDataTransfer(data).length > 0;
}

export function extractFilesFromDataTransfer(
  data: DataTransferFileLike | null | undefined,
): File[] {
  const direct = iterableToArray(data?.files).filter((file): file is File => file instanceof File);
  if (direct.length) return direct;
  const files: File[] = [];
  for (const item of iterableToArray(data?.items)) {
    if (String(item?.kind || '') !== 'file' || typeof item?.getAsFile !== 'function') continue;
    const file = item.getAsFile();
    if (file instanceof File) files.push(file);
  }
  return files;
}
