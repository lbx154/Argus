// Files the operator attaches to a message, kept in the workdir under .argus/attachments/<id>/,
// where the Manager reads them: the message that carries them names their paths. Small files
// come in one request; a large one in parts, each appended where the last ended, to a `.part`
// that becomes the file once all of it has come. Nothing checks what a file holds.

import { randomBytes } from 'node:crypto';
import { appendFileSync, mkdirSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import type { IncomingMessage } from 'node:http';
import { join } from 'node:path';
import type { Project } from '../store.js';
import { HttpError, type Handler } from './index.js';

const LIMITS = { max_count: 5, max_bytes_per_file: 100 << 20, max_total_bytes: 500 << 20, chunk_bytes: 4 << 20 };
/** What one request may carry: the page sends up to 25 MiB of files at once, larger ones in parts. */
const ONE_REQUEST = 26 << 20;
const ID = /^att-[0-9a-f]{12}$/;
const PART = '.part';

interface Upload {
  sid: string;
  dir: string;
  attachment: { attachment_id: string; relative_path: string; original_name: string; stored_name: string; mime: string; size_bytes: number };
}

const folder = (workdir: string, id: string): string => join(workdir, '.argus', 'attachments', id);

const listed = (dir: string): string[] => { try { return readdirSync(dir); } catch { return []; } };

/** What a message says of the files it carries: where each is in the workdir. */
export function attached({ workdir }: Project, refs: unknown): string {
  if (!Array.isArray(refs) || !refs.length) return '';
  const paths = refs.map(ref => {
    const id = String((ref as { attachment_id?: unknown } | null)?.attachment_id ?? '');
    const name = ID.test(id) ? listed(folder(workdir, id)).find(name => name !== PART) : undefined;
    if (!name) throw new HttpError(400, `no attachment ${id} has been uploaded to this project`);
    return `- .argus/attachments/${id}/${name}`;
  });
  return `\n\nAttached, in the workdir:\n${paths.join('\n')}`;
}

export function attachments(project: (id: string) => Project): [string, RegExp, Handler][] {
  /** The uploads begun here, by id. */
  const uploads = new Map<string, Upload>();
  const declare = (work: Project, name: string, mime: string, size: number): Upload => {
    if (size > LIMITS.max_bytes_per_file) throw new HttpError(413, `${name} is larger than ${LIMITS.max_bytes_per_file} bytes`);
    const id = `att-${randomBytes(6).toString('hex')}`;
    // The name as given, as one the file system takes and never hidden.
    const stored = name.split(/[\\/]/).at(-1)!.replace(/[\x00-\x1f<>:"|?*]/g, '_').replace(/^[.\s]+/, '').slice(-200) || 'attachment';
    return {
      sid: work.id, dir: folder(work.workdir, id),
      attachment: { attachment_id: id, relative_path: `.argus/attachments/${id}/${stored}`, original_name: name, stored_name: stored, mime, size_bytes: size },
    };
  };
  const keep = (upload: Upload, file: string, content: Buffer | string): Upload => {
    mkdirSync(upload.dir, { recursive: true });
    writeFileSync(join(upload.dir, file), content);
    uploads.set(upload.attachment.attachment_id, upload);
    return upload;
  };
  const upload = (id: string, attachment: string): Upload => {
    const found = uploads.get(attachment);
    if (!found || found.sid !== project(id).id) throw new HttpError(404, 'upload not found for this project');
    return found;
  };
  /** How much of an upload has come; once all of it, its file. */
  const state = ({ dir, attachment }: Upload) => {
    const { attachment_id, size_bytes } = attachment;
    const part = statSync(join(dir, PART), { throwIfNoEntry: false });
    return part ? { attachment_id, offset: part.size, size_bytes, complete: false }
      : { attachment_id, offset: size_bytes, size_bytes, complete: true, attachment };
  };
  const UPLOAD = /^\/api\/projects\/([^/]+)\/attachments\/uploads\/([^/]+)$/;
  return [
    ['GET', /^\/api\/projects\/([^/]+)\/attachments\/limits$/, ({ params: [id] }) => (project(id!), LIMITS)],
    ['POST', /^\/api\/projects\/([^/]+)\/attachments$/, async ({ params: [id], request }) => {
      const work = project(id!);
      const form = await new Response(await bytes(request, ONE_REQUEST), { headers: { 'content-type': request.headers['content-type'] ?? '' } })
        .formData().catch(() => { throw new HttpError(400, 'files are sent as multipart/form-data'); });
      const files = form.getAll('files').filter(file => typeof file !== 'string');
      if (!files.length || files.length > LIMITS.max_count) throw new HttpError(400, `send 1 to ${LIMITS.max_count} files`);
      const declared = files.map(file => declare(work, file.name, file.type, file.size));
      for (const [index, file] of files.entries()) keep(declared[index]!, declared[index]!.attachment.stored_name, Buffer.from(await file.arrayBuffer()));
      return { attachments: declared.map(({ attachment }) => attachment), limits: LIMITS };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/attachments\/uploads$/, async ({ params: [id], body }) => {
      const { name, mime, size_bytes: size } = await body();
      if (typeof name !== 'string' || !name || typeof size !== 'number' || !Number.isSafeInteger(size) || size < 0) {
        throw new HttpError(400, 'an upload begins with { name, mime, size_bytes }');
      }
      return state(keep(declare(project(id!), name, String(mime ?? ''), size), PART, ''));
    }],
    ['GET', UPLOAD, ({ params: [id, at] }) => state(upload(id!, at!))],
    ['PUT', UPLOAD, async ({ params: [id, at], url, request }) => {
      const found = upload(id!, at!);
      const content = await bytes(request, LIMITS.chunk_bytes);
      const { offset, size_bytes, complete } = state(found);
      if (complete) throw new HttpError(409, 'upload is already complete');
      if (Number(url.searchParams.get('offset')) !== offset) throw new HttpError(409, `upload offset conflict; expected ${offset}`);
      if (!content.length || offset + content.length > size_bytes) throw new HttpError(413, 'chunk exceeds the declared file size');
      appendFileSync(join(found.dir, PART), content);
      return state(found);
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/attachments\/uploads\/([^/]+)\/complete$/, ({ params: [id, at] }) => {
      const found = upload(id!, at!);
      const { offset, size_bytes, complete } = state(found);
      if (complete) return state(found);
      if (offset !== size_bytes) throw new HttpError(409, `upload is incomplete; received ${offset} bytes`);
      renameSync(join(found.dir, PART), join(found.dir, found.attachment.stored_name));
      return state(found);
    }],
    ['DELETE', UPLOAD, ({ params: [id, at] }) => {
      rmSync(upload(id!, at!).dir, { recursive: true, force: true });
      uploads.delete(at!);
      return { cancelled: true };
    }],
    // A file the operator took out of the draft before sending it.
    ['DELETE', /^\/api\/projects\/([^/]+)\/attachments\/([^/]+)$/, ({ params: [id, at] }) => {
      if (!ID.test(at!)) throw new HttpError(400, `invalid attachment_id: ${at}`);
      rmSync(folder(project(id!).workdir, at!), { recursive: true, force: true });
      uploads.delete(at!);
      return { discarded: true, attachment_id: at };
    }],
  ];
}

async function bytes(request: IncomingMessage, limit: number): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request as AsyncIterable<Buffer>) {
    if ((size += chunk.length) > limit) throw new HttpError(413, 'request body too large');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}
