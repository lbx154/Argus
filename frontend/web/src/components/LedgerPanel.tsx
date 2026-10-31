import { useQuery } from '@tanstack/react-query';
import { ApiError } from '../../../core/src/http';
import { api } from '../api';
import { useI18n } from '../i18n';
import { TablePreview } from './DataPreview';
import { ErrorMessage } from './ErrorMessage';

export function LedgerPanel({ sid }: { sid: string }) {
  const { locale } = useI18n();
  const document = useQuery({
    queryKey: ['skill-document', sid, 'shared', `runs/${sid}.tsv`],
    queryFn: ({ signal }) => api.skillDocument(sid, 'shared', `runs/${sid}.tsv`, signal),
    retry: false,
  });
  if (document.error instanceof ApiError && document.error.status === 404) return null;
  if (document.isPending) return null;
  return <section className="min-w-0 rounded-lg border border-line/60 bg-surface p-3" data-ledger-panel>
    <h3 className="mb-3 text-sm font-medium text-ink">{locale === 'zh-CN' ? '项目台账' : 'Project ledger'}</h3>
    {document.isError ? <div role="alert"><ErrorMessage error={document.error} /></div>
      : <TablePreview value={document.data.content} delimiter={'\t'} />}
  </section>;
}
