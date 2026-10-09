import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { useConfig } from '../hooks';
import { useI18n } from '../i18n';
import { noOperatorMode, OPERATOR_AVAILABLE_KNOB } from '../lib/configSurface';

/**
 * A present user must never have questions suppressed without knowing it.
 * When the saved operator switch is off, the cockpit says so at the top and
 * offers to turn questions back on.
 */
export function NoOperatorBanner({ sid }: { sid: string | null }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const { data } = useConfig(sid, true);
  const [busy, setBusy] = useState(false);
  if (!sid || !noOperatorMode(data?.operator_knobs)) return null;
  const restore = async () => {
    setBusy(true);
    try {
      await api.setConfig(sid, OPERATOR_AVAILABLE_KNOB, '1');
      await queryClient.invalidateQueries({ queryKey: ['config', sid] });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      data-no-operator-banner
      role="status"
      className="mx-3 mt-3 flex items-center gap-2.5 rounded-lg border border-warn/50 bg-warn/10 px-3.5 py-2 text-[13px] text-warn"
    >
      <span className="min-w-0 flex-1">{t('noOperator.banner')}</span>
      <button
        type="button"
        onClick={() => void restore()}
        disabled={busy}
        className="shrink-0 rounded border border-warn/50 px-2 py-0.5 text-xs font-medium hover:bg-warn/10 disabled:opacity-40"
      >
        {t('noOperator.turnOff')}
      </button>
    </div>
  );
}
