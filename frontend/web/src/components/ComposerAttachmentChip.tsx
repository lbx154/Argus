import { useEffect, useState } from 'react';
import { attachmentMime, isImageAttachment } from '../lib/attachments';
import { formatBytes } from '../lib/format';

export function ComposerAttachmentChip({
  file,
  removeLabel,
  onRemove,
}: {
  file: File;
  removeLabel: string;
  onRemove: () => void;
}) {
  const [previewUrl, setPreviewUrl] = useState('');

  useEffect(() => {
    if (
      !isImageAttachment(file)
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

  return (
    <div className="flex min-w-0 items-center gap-2 rounded-2xl border border-line/50 bg-panel/80 px-2.5 py-2 text-xs shadow-[0_10px_24px_-20px_rgb(0_0_0/0.2)]">
      {previewUrl ? (
        <img
          src={previewUrl}
          alt=""
          className="h-10 w-10 shrink-0 rounded-xl border border-line/40 object-cover"
        />
      ) : null}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium text-ink" title={file.name}>{file.name}</div>
        <div className="truncate text-ink-faint">
          {formatBytes(file.size)} · {attachmentMime(file)}
        </div>
      </div>
      <button
        type="button"
        onClick={onRemove}
        aria-label={removeLabel}
        title={removeLabel}
        className="send-control h-8 w-8 shrink-0 rounded-full border-line/60 text-ink-faint hover:border-err/50 hover:bg-err/10 hover:text-err"
      >
        ×
      </button>
    </div>
  );
}
