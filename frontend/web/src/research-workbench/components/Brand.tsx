export function ArgusMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 512 512" role="img" aria-label="Argus" style={{ width: size, height: size }}>
      <path d="M352 112q0-30 30-30h28q30 0 30 30v320h-88v-52q-46 62-129 62Q66 442 66 266T228 88q80 0 124 56v-32ZM140 266q46-80 102-80t110 80q-54 80-110 80t-102-80Z" fill="currentColor" fillRule="evenodd" />
      <path d="M286 266A42 42 0 1 0 202 266A42 42 0 1 0 286 266ZM274 248A12 12 0 1 0 250 248A12 12 0 1 0 274 248Z" fill="currentColor" fillRule="evenodd" />
    </svg>
  );
}

export function ArgusWordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className="argus-wordmark">
      <ArgusMark size={compact ? 28 : 31} />
      {!compact ? (
        <span className="argus-wordmark__copy">
          <strong>Argus</strong>
          <small>Workbench</small>
        </span>
      ) : null}
    </span>
  );
}
