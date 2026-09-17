import { ArgusMark as SharedArgusMark } from '../../components/Wordmark';

export function ArgusMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  return <SharedArgusMark size={size} className={className} />;
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
