import type { UploadedAttachment } from '../api';

export const MESSAGE_TEXT_MAX_CHARS = 32_000;
export interface AttachmentLimits {
  max_count: number;
  max_bytes_per_file: number;
  max_total_bytes: number;
  chunk_bytes: number;
}

export type ComposerAttachmentIssue =
  | { code: 'too-large'; fileName: string; limitBytes: number }
  | { code: 'too-many'; limitCount: number }
  | { code: 'too-large-total'; limitBytes: number };

export function attachmentMime(file: Pick<File, 'type'>): string {
  return file.type || 'application/octet-stream';
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
  limits?: AttachmentLimits,
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
    if (limits && count >= limits.max_count) {
      issues.push({ code: 'too-many', limitCount: limits.max_count });
      continue;
    }
    if (limits && file.size > limits.max_bytes_per_file) {
      issues.push({
        code: 'too-large',
        fileName: file.name,
        limitBytes: limits.max_bytes_per_file,
      });
      continue;
    }
    if (limits && totalBytes + file.size > limits.max_total_bytes) {
      issues.push({
        code: 'too-large-total',
        limitBytes: limits.max_total_bytes,
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

/** Old records stored the upload paths at the end of the operator's words. */
export function splitMessageAttachments(text: string, attachments?: UploadedAttachment[]): { text: string; attachments: UploadedAttachment[] } {
  if (attachments) return { text, attachments };
  const tail = /\n\nAttached, in the workdir:\n((?:- \.argus\/attachments\/att-[0-9a-f]{12}\/[^\r\n]+(?:\n|$))+)$/.exec(text);
  if (!tail) return { text, attachments: [] };
  return {
    text: text.slice(0, tail.index),
    attachments: tail[1].trimEnd().split('\n').map(line => {
      const relative_path = line.slice(2);
      const [, , attachment_id, ...parts] = relative_path.split('/');
      const name = parts.join('/');
      return { attachment_id, relative_path, original_name: name, stored_name: name, mime: attachmentMime({ type: '' }), size_bytes: 0 };
    }),
  };
}
