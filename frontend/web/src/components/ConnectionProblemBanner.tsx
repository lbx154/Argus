import { isAuthenticationError, LocalArgusUnavailableError, pairWithToken } from '../api';
import { errorText } from '../lib/format';
import { useI18n } from '../i18n';
import { canOpenDesktopSettings } from '../lib/desktopBridge';
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
  const [pairingInput, setPairingInput] = useState('');
  const [pairingError, setPairingError] = useState('');
  const [connecting, setConnecting] = useState(false);
  if (!pairing && !unavailable) return null;

  const repairPairing = async (event: FormEvent) => {
    event.preventDefault();
    const token = pairingTokenFromInput(pairingInput);
    if (!token) {
      setPairingError(t('connection.pairingInvalid'));
      return;
    }
    setConnecting(true);
    try {
      if (await pairWithToken(token)) return;
      setPairingError(t('connection.pairingRejected'));
    } catch (problem) {
      setPairingError(errorText(problem));
    }
    setConnecting(false);
  };

  if (pairing) {
    // Nothing works until this browser has its access link: one centered card
    // with the paste box already open, instead of a banner over an empty page.
    return (
      <div role="alert" className="fixed inset-0 z-[100] flex items-center justify-center bg-bg/95 px-4 backdrop-blur">
        <div className="w-full max-w-md rounded-2xl border border-line bg-panel p-6 text-left shadow-2xl">
          <h1 className="text-base font-semibold text-ink">{t('connection.pairingTitle')}</h1>
          <p className="mt-2 text-sm leading-relaxed text-ink-dim">{t('connection.pairingDetail')}</p>
          <form onSubmit={event => void repairPairing(event)} className="mt-4 flex flex-col gap-2">
            <label className="text-xs font-medium text-ink-dim" htmlFor="pairing-link">{t('connection.pairingInput')}</label>
            <div className="flex gap-2">
              <input
                id="pairing-link"
                data-autofocus
                autoFocus
                type="password"
                autoComplete="off"
                value={pairingInput}
                onChange={(event) => {
                  setPairingInput(event.target.value);
                  setPairingError('');
                }}
                placeholder={t('connection.pairingPlaceholder')}
                className="h-10 min-w-0 flex-1 rounded-md border border-line bg-bg px-3 text-sm text-ink outline-none focus:border-blue"
              />
              <button type="submit" disabled={connecting} className="h-10 shrink-0 rounded-md border border-blue/45 bg-blue px-4 text-sm font-medium text-on-accent hover:bg-blue-deep disabled:opacity-50">
                {t('connection.connect')}
              </button>
            </div>
            {pairingError ? <span role="alert" className="text-xs text-err">{pairingError}</span> : null}
          </form>
          <p className="mt-4 text-xs leading-relaxed text-ink-faint">{t('connection.pairingHelp')}</p>
        </div>
      </div>
    );
  }

  return (
    <div
      role="alert"
      className="fixed left-1/2 top-3 z-[100] flex w-[min(92vw,42rem)] -translate-x-1/2 flex-wrap items-start gap-3 rounded-xl border border-err/50 bg-panel/95 px-4 py-3 text-left text-sm text-ink shadow-xl backdrop-blur"
    >
      <span aria-hidden="true" className="mt-0.5 font-mono font-bold text-err">!</span>
      <div className="min-w-0 flex-1">
        <strong className="block text-err">{t('connection.unreachableTitle')}</strong>
        <span className="mt-0.5 block text-xs leading-relaxed text-ink-dim">
          {t(canOpenDesktopSettings() ? 'connection.unreachableDetail' : 'connection.unreachableDetailWeb')}
        </span>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 rounded-md border border-err/40 px-2.5 py-1 text-xs text-err hover:bg-err/10"
      >
        {t('common.retry')}
      </button>
    </div>
  );
}
