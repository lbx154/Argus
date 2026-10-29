import { useEffect, useState } from 'react';
import type { UploadedAttachment } from '../api';
import { useI18n } from '../i18n';
import { attachmentMime, isImageAttachment } from '../lib/attachments';
import { uploadPercent, useDraftUploadView } from '../lib/draftUploads';
import { errorText, formatBytes } from '../lib/format';
import { ErrorDetails } from './ErrorMessage';

/** One draft attachment: what it is, and how far its upload has got. */
export function ComposerAttachmentChip({
  file,
  uploaded,
  removeLabel,
  onRemove,
  disabled = false,
}: {
  file?: File;
  /** A file uploaded before a reload, when only the project copy remains. */
  uploaded?: UploadedAttachment;
  removeLabel: string;
  onRemove: () => void;
  disabled?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const uploads = useDraftUploadView();
  const [previewUrl, setPreviewUrl] = useState('');

  useEffect(() => {
    if (
      !file
      || !isImageAttachment(file)
      || file.size > 4 * 1024 * 1024
      || typeof URL === 'undefined'
      || typeof URL.createObjectURL !== 'function'
    ) {
      setPreviewUrl('');
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    setPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  const name = file?.name ?? uploaded?.original_name ?? '';
  const size = file?.size ?? uploaded?.size_bytes ?? 0;
  const status = file ? uploads?.status(file) : undefined;
  const phase = uploaded ? 'ready' : status?.phase;
  const percent = uploadPercent(status);
  const progress = phase === 'queued' ? (zh ? '等待上传' : 'Waiting to upload')
    : phase === 'uploading' ? `${zh ? '上传中' : 'Uploading'} ${percent}% · ${formatBytes(status?.loaded ?? 0)}/${formatBytes(size)}`
      : phase === 'retrying' ? (zh ? `网络不稳，正在重试 · ${percent}%` : `Connection unstable, retrying · ${percent}%`)
        : phase === 'validating' ? (zh ? '已传完，正在校验' : 'Uploaded, checking the file')
          : phase === 'ready' ? (zh ? '✓ 已上传' : '✓ Uploaded')
            : phase === 'failed' ? (zh ? '上传失败' : 'Upload failed') : '';
  const failure = phase === 'failed' ? errorText(status?.error) : '';
  const moving = phase === 'queued' || phase === 'uploading' || phase === 'retrying' || phase === 'validating';

  return (
    <div data-upload-phase={phase ?? 'none'} title={failure || name}
      className={`relative flex min-w-0 items-center gap-2 overflow-hidden rounded-2xl border bg-panel/80 px-2.5 py-2 text-xs shadow-[0_10px_24px_-20px_rgb(0_0_0/0.2)] ${phase === 'failed' ? 'border-err/50' : 'border-line/50'}`}>
      {previewUrl ? (
        <img
          src={previewUrl}
          alt=""
          className="h-10 w-10 shrink-0 rounded-xl border border-line/40 object-cover"
        />
      ) : null}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium text-ink">{name}</div>
        <div className={`truncate ${phase === 'failed' ? 'text-err' : phase === 'ready' ? 'text-ok' : 'text-ink-faint'}`} role={phase === 'failed' ? 'alert' : undefined}>
          {phase === 'uploading' || phase === 'retrying' ? progress
            : [formatBytes(size), progress || (file ? attachmentMime(file) : uploaded?.mime), failure].filter(Boolean).join(' · ')}
        </div>
        {phase === 'failed' ? <ErrorDetails error={status?.error} /> : null}
      </div>
      {phase === 'failed' && file ? (
        <button type="button" onClick={() => uploads?.retry(file)} disabled={disabled}
          className="shrink-0 rounded-full border border-line/60 px-2 py-1 text-[11px] text-ink-dim hover:text-ink">
          {zh ? '重试' : 'Retry'}
        </button>
      ) : null}
      <button
        type="button"
        onClick={onRemove}
        disabled={disabled}
        aria-label={removeLabel}
        title={removeLabel}
        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line/60 text-ink-faint transition-colors hover:border-err/50 hover:bg-err/10 hover:text-err disabled:opacity-50"
      >
        ×
      </button>
      {moving ? (
        <div className="absolute inset-x-0 bottom-0 h-[3px] bg-line/40" role="progressbar" aria-label={zh ? `${name} 上传进度` : `${name} upload progress`}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
          <div className={`h-full bg-blue transition-[width] duration-300 ease-out ${phase === 'queued' || phase === 'validating' ? 'animate-pulse' : ''}`}
            style={{ width: `${phase === 'queued' ? 0 : percent}%` }} />
        </div>
      ) : null}
    </div>
  );
}
