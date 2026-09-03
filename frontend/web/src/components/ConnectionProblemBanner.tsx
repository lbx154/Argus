import { isAuthenticationError, LocalArgusUnavailableError } from '../api';
import { useI18n } from '../i18n';
import { useState, type FormEvent } from 'react';

export function pairingTokenFromInput(input: string): string {
  const value = input.trim();
  if (!value || /\s/.test(value)) return '';
  if (!value.includes('?') && !value.includes('://')) return value;
  try {
    const parsed = new URL(value, window.location.href);
    return parsed.searchParams.get('token')?.trim() ?? '';
  } catch {
    return '';
  }
}

export function ConnectionProblemBanner({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const { t } = useI18n();
  const pairing = isAuthenticationError(error);
  const unavailable = error instanceof LocalArgusUnavailableError;
  const [repairOpen, setRepairOpen] = useState(false);
  const [pairingInput, setPairingInput] = useState('');
  const [pairingError, setPairingError] = useState('');
  if (!pairing && !unavailable) return null;

  const repairPairing = (event: FormEvent) => {
    event.preventDefault();
    const token = pairingTokenFromInput(pairingInput);
    if (!token) {
      setPairingError(t('connection.pairingInvalid'));
      return;
    }
    const target = new URL(window.location.href);
    target.searchParams.set('token', token);
    window.location.replace(target.toString());
  };

  return (
    <div
      role="alert"
      className="fixed left-1/2 top-3 z-[100] flex w-[min(92vw,42rem)] -translate-x-1/2 flex-wrap items-start gap-3 rounded-xl border border-err/50 bg-panel/95 px-4 py-3 text-left text-sm text-ink shadow-xl backdrop-blur"
    >
      <span aria-hidden="true" className="mt-0.5 font-mono font-bold text-err">!</span>
      <div className="min-w-0 flex-1">
        <strong className="block text-err">
          {t(pairing ? 'connection.pairingTitle' : 'connection.unreachableTitle')}
        </strong>
        <span className="mt-0.5 block text-xs leading-relaxed text-ink-dim">
          {t(pairing ? 'connection.pairingDetail' : 'connection.unreachableDetail')}
        </span>
      </div>
      {pairing && !repairOpen ? (
        <button
          type="button"
          onClick={() => setRepairOpen(true)}
          className="shrink-0 rounded-md border border-blue/45 bg-blue/10 px-2.5 py-1 text-xs font-medium text-blue hover:bg-blue/15"
        >
          {t('connection.pairAgain')}
        </button>
      ) : !pairing ? (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded-md border border-err/40 px-2.5 py-1 text-xs text-err hover:bg-err/10"
        >
          {t('common.retry')}
        </button>
      ) : null}
      {pairing && repairOpen ? (
        <form onSubmit={repairPairing} className="flex w-full basis-full flex-wrap gap-2 pl-7">
          <label className="sr-only" htmlFor="pairing-link">{t('connection.pairingInput')}</label>
          <input
            id="pairing-link"
            data-autofocus
            type="password"
            autoComplete="off"
            value={pairingInput}
            onChange={(event) => {
              setPairingInput(event.target.value);
              setPairingError('');
            }}
            placeholder={t('connection.pairingPlaceholder')}
            className="h-9 min-w-0 flex-1 rounded-md border border-line bg-bg px-3 text-xs text-ink outline-none focus:border-blue"
          />
          <button type="submit" className="h-9 rounded-md border border-blue/35 bg-blue/8 px-3 text-xs font-medium text-blue hover:border-blue-deep hover:bg-blue-deep hover:text-white">
            {t('connection.connect')}
          </button>
          {pairingError ? <span role="alert" className="w-full text-xs text-err">{pairingError}</span> : null}
        </form>
      ) : null}
    </div>
  );
}
