import { createContext, useContext } from 'react';
import type { AttachmentUploadOptions, AttachmentUploadResponse, UploadedAttachment } from '../api';

/** Where one chosen file is on its way to the project, shown on its chip. */
export type DraftUploadPhase = 'queued' | 'uploading' | 'validating' | 'retrying' | 'ready' | 'failed';
export interface DraftUploadStatus {
  phase: DraftUploadPhase;
  loaded: number;
  total: number;
  error?: unknown;
}

type Upload = (sid: string, files: File[], signal: AbortSignal, options: AttachmentUploadOptions) => Promise<AttachmentUploadResponse>;
type Discard = (sid: string, attachmentId: string) => Promise<unknown>;

interface Entry {
  sid: string;
  status: DraftUploadStatus;
  controller: AbortController;
  attachment?: UploadedAttachment;
  done: Promise<UploadedAttachment>;
  /** Sends waiting on this file; it is theirs even if the draft drops it. */
  claims: number;
  cancelled: boolean;
}

const abortError = () => new DOMException('Aborted', 'AbortError');

/**
 * Files upload as soon as they are chosen, one at a time, so sending only
 * waits for whatever is still in flight. A file removed from the draft is
 * cancelled, or deleted from the project if it had already arrived.
 */
export class DraftUploads {
  private entries = new Map<File, Entry>();
  private tail: Promise<unknown> = Promise.resolve();
  private listeners = new Set<() => void>();

  constructor(
    private readonly upload: Upload,
    private readonly discard: Discard,
    private readonly onUploaded: (sid: string, file: File, attachment: UploadedAttachment) => void,
  ) {}

  subscribe(listener: () => void) {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private changed() {
    for (const listener of this.listeners) listener();
  }

  status(file: File): DraftUploadStatus | undefined {
    return this.entries.get(file)?.status;
  }

  /** Start uploading every file that is neither uploaded nor on its way. */
  ensure(sid: string, files: File[], completed: ReadonlyMap<File, UploadedAttachment> = new Map()) {
    let started = false;
    for (const file of files) {
      const entry = this.entries.get(file);
      if (completed.has(file) || (entry && entry.sid === sid && entry.status.phase !== 'failed')) continue;
      this.start(sid, file);
      started = true;
    }
    if (started) this.changed();
  }

  retry(file: File) {
    const entry = this.entries.get(file);
    if (entry?.status.phase !== 'failed') return;
    this.start(entry.sid, file);
    this.changed();
  }

  private start(sid: string, file: File) {
    const controller = new AbortController();
    const entry = { sid, controller, claims: 0, cancelled: false, status: { phase: 'queued', loaded: 0, total: file.size } } as Entry;
    const run = async (): Promise<UploadedAttachment> => {
      if (controller.signal.aborted) throw abortError();
      const result = await this.upload(sid, [file], controller.signal, {
        onProgress: (progress) => {
          if (entry.cancelled || progress.phase === 'ready') return;
          entry.status = { phase: progress.phase, loaded: progress.loaded, total: progress.total };
          this.changed();
        },
      });
      const attachment = result.attachments[0];
      if (!attachment) throw new Error('Server did not return a completed attachment');
      if (entry.cancelled) {
        // Removed while its last bytes were committing: delete it as well.
        void this.discard(sid, attachment.attachment_id).catch(() => undefined);
        throw abortError();
      }
      return attachment;
    };
    const done = this.tail.then(run, run);
    this.tail = done.catch(() => undefined);
    entry.done = done.then((attachment) => {
      entry.attachment = attachment;
      entry.status = { phase: 'ready', loaded: file.size, total: file.size };
      this.onUploaded(sid, file, attachment);
      this.changed();
      return attachment;
    }, (error: unknown) => {
      if (!entry.cancelled) {
        entry.status = { ...entry.status, phase: 'failed', error };
        this.changed();
      }
      throw error;
    });
    entry.done.catch(() => undefined);
    this.entries.set(file, entry);
  }

  /** The draft no longer has this file: stop it, or delete what arrived. */
  cancel(file: File) {
    const entry = this.entries.get(file);
    if (!entry) return;
    this.entries.delete(file);
    if (entry.claims === 0) {
      entry.cancelled = true;
      entry.controller.abort();
      if (entry.attachment) void this.discard(entry.sid, entry.attachment.attachment_id).catch(() => undefined);
    }
    this.changed();
  }

  /** Sent files belong to their message now; stop tracking them. */
  forget(files: File[]) {
    let changed = false;
    for (const file of files) changed = this.entries.delete(file) || changed;
    if (changed) this.changed();
  }

  /**
   * Wait until every file has arrived. Aborting the wait leaves the uploads
   * running; a failed upload rejects so nothing is sent without it.
   */
  async settle(sid: string, files: File[], completed: ReadonlyMap<File, UploadedAttachment>, signal: AbortSignal) {
    this.ensure(sid, files, completed);
    const claimed = files.flatMap(file => completed.has(file) ? [] : [this.entries.get(file)!]);
    for (const entry of claimed) entry.claims += 1;
    try {
      const pending = files.map(async (file) => completed.get(file) ?? this.entries.get(file)!.done);
      const aborted = new Promise<never>((_resolve, reject) => {
        if (signal.aborted) reject(abortError());
        signal.addEventListener('abort', () => reject(abortError()), { once: true });
      });
      aborted.catch(() => undefined);
      const attachments = await Promise.race([Promise.all(pending), aborted]);
      return new Map(files.map((file, index) => [file, attachments[index]] as const));
    } finally {
      for (const entry of claimed) entry.claims -= 1;
    }
  }

  /** Files of one project draft still on their way. */
  busy(files: File[]) {
    return files.filter((file) => {
      const phase = this.entries.get(file)?.status.phase;
      return phase !== undefined && phase !== 'ready' && phase !== 'failed';
    }).length;
  }
}

export interface DraftUploadView {
  limits?: import('./attachments').AttachmentLimits;
  status: (file: File) => DraftUploadStatus | undefined;
  retry: (file: File) => void;
  /** Uploaded before a reload: no bytes in memory, only the project file. */
  ready: UploadedAttachment[];
  removeReady: (attachmentId: string) => void;
  /** How many files of this draft are still uploading. */
  busy: number;
}

export const DraftUploadContext = createContext<DraftUploadView | null>(null);
export const useDraftUploadView = () => useContext(DraftUploadContext);

export function uploadPercent(status: DraftUploadStatus | undefined): number {
  if (!status) return 0;
  if (status.phase === 'ready' || status.phase === 'validating') return 100;
  return Math.floor(status.loaded / Math.max(1, status.total) * 100);
}
