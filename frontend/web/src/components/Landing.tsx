import { BackendHandshake } from './BackendHandshake';
import { Wordmark } from './Wordmark';
import { Button } from './primitives';
import { TAGLINE } from '../lib/soul';

/** Full-viewport picker/empty landing shown until a daemon is selectable. */
export function Landing({
  loading,
  hasProjects,
  error,
  onRetry,
  onNew,
  onChoose,
  canCreate,
}: {
  loading: boolean;
  hasProjects: boolean;
  error?: string;
  onRetry: () => void;
  onNew: () => void;
  onChoose: () => void;
  canCreate: boolean;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
      {loading ? <BackendHandshake /> : (
        <>
          <Wordmark size={32} tag={TAGLINE} />
          <p className={`max-w-md text-sm leading-relaxed ${error ? 'text-err' : 'text-ink-faint'}`}>
            {error
              ? error
              : hasProjects
              ? 'Select a session from the sidebar, or create a new one.'
              : 'No sessions yet. Create one to begin.'}
          </p>
        </>
      )}
      {!loading && (
        <div className="flex flex-wrap justify-center gap-2">
          {error ? (
            <Button onClick={onRetry} variant="danger">Retry</Button>
          ) : null}
          {hasProjects ? (
            <Button onClick={onChoose}>Select session</Button>
          ) : canCreate ? (
            <Button onClick={onNew} variant="primary">New session</Button>
          ) : null}
        </div>
      )}
    </div>
  );
}
