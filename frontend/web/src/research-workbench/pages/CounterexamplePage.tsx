import { CounterexampleLab } from '../../components/CounterexampleLab';
import type {
  CounterexampleConjecture,
  CounterexampleEvidence,
  CounterexampleStage,
  CounterexampleStageStatus,
  CounterexampleStatus,
} from '../../lib/counterexampleProgress';
import { useWorkbenchText } from '../useWorkbenchText';
import type { CounterexampleCandidate } from '../types';
import type { WorkspacePageProps } from './pageTypes';

const STAGES = ['scope', 'source', 'construct', 'review', 'result'] as const;
type StageId = typeof STAGES[number];

function stageStatus(candidate: CounterexampleCandidate, stage: StageId): CounterexampleStageStatus {
  const status = candidate.status;
  if (stage === 'scope') return status === 'queued' ? 'pending' : 'done';
  if (stage === 'source') {
    if (status === 'source-review' || status === 'scoped') return 'active';
    return status === 'queued' ? 'pending' : 'done';
  }
  if (stage === 'construct') {
    if (status === 'constructing') return 'active';
    if (['evidence', 'reviewing', 'verified', 'rejected'].includes(status)) return 'done';
    return 'pending';
  }
  if (stage === 'review') {
    if (status === 'evidence' || status === 'reviewing') return 'active';
    if (status === 'verified' || status === 'rejected') return 'done';
    return 'pending';
  }
  return status === 'verified' || status === 'rejected' ? 'done' : 'pending';
}

function conjectureStatus(candidate: CounterexampleCandidate, active: boolean): CounterexampleStatus {
  if (candidate.status === 'verified') return 'refuted';
  if (candidate.status === 'rejected') return 'inconclusive';
  return active ? 'active' : 'queued';
}

function buildStages(candidate: CounterexampleCandidate, labels: Record<StageId, string>): CounterexampleStage[] {
  return STAGES.map((id) => ({
    id,
    label: labels[id],
    status: stageStatus(candidate, id),
    progress: id === 'construct' && candidate.status === 'constructing' ? 55 : undefined,
    updatedAt: candidate.updated_at || undefined,
  }));
}

function buildEvidence(candidate: CounterexampleCandidate): CounterexampleEvidence[] {
  const rows: CounterexampleEvidence[] = [];
  if (candidate.evidence_path) {
    rows.push({
      id: `${candidate.id}-evidence`,
      title: candidate.evidence_path,
      kind: 'paper',
      status: candidate.status === 'verified' ? 'verified' : 'candidate',
      summary: candidate.result_summary || candidate.rejection_reason,
      source: candidate.disposition || candidate.verification_level,
      updatedAt: candidate.updated_at || undefined,
    });
  }
  if (candidate.parallel_files) {
    rows.push({
      id: `${candidate.id}-parallel`,
      title: `${candidate.parallel_files} parallel artifact${candidate.parallel_files === 1 ? '' : 's'}`,
      kind: 'computation',
      status: 'candidate',
      summary: 'A parallel worker is constructing a witness or recording a bounded negative result.',
      updatedAt: candidate.updated_at || undefined,
    });
  }
  return rows;
}

export function CounterexamplePage(props: WorkspacePageProps) {
  const { text } = useWorkbenchText();
  const activeText = [
    props.snapshot.mission_view?.mission.title,
    props.snapshot.mission_view?.mission.objective,
    ...(props.snapshot.backlog ?? [])
      .filter((item) => ['running', 'in_progress', 'claimed'].includes(item.status))
      .flatMap((item) => [item.title, item.objective]),
  ].filter(Boolean).join(' ');
  const labels: Record<StageId, string> = {
    scope: text('命题对齐', 'Scope'),
    source: text('原始来源', 'Primary source'),
    construct: text('构造反例', 'Construct'),
    review: text('独立复核', 'Review'),
    result: text('结果入库', 'Result'),
  };
  const conjectures: CounterexampleConjecture[] = (
    props.counterexamples?.candidates ?? []
  ).map((candidate) => {
    const active = activeText.includes(candidate.id);
    const stages = buildStages(candidate, labels);
    return {
      id: candidate.id,
      title: candidate.title,
      shortTitle: `${candidate.id} · ${candidate.title.split('（')[0]}`,
      statement: candidate.description,
      field: `${candidate.classification} · source ${candidate.source_grade || '—'}`,
      status: conjectureStatus(candidate, active),
      active,
      live: props.connected && active,
      progress: candidate.progress,
      currentStageId: stages.find((stage) => ['active', 'running', 'in_progress', 'claimed'].includes(stage.status))?.id,
      stages,
      evidence: buildEvidence(candidate),
      activity: active ? {
        label: props.snapshot.mission_view?.mission.title || text('Argus 正在处理', 'Argus is working'),
        detail: props.snapshot.mission_view?.mission.objective || '',
        actor: props.snapshot.mission_view?.active_role || props.status?.active_role || 'Argus',
      } : undefined,
      updatedAt: candidate.updated_at || props.counterexamples?.generated_at,
    };
  });

  return (
    <div className="p-3 sm:p-5">
      <CounterexampleLab
        conjectures={conjectures}
        title={text('反例实验室', 'Counterexample Lab')}
        subtitle={text('实时查看 Argus 对每个猜想的命题对齐、来源核查、反例构造、独立复核和最终入库状态。', 'Track proposition alignment, sources, counterexample construction, independent review, and accepted results in real time.')}
        live={props.connected}
        lastUpdatedAt={props.counterexamples?.generated_at}
        conjecturesLabel={text('猜想列表', 'Conjectures')}
        currentActivityLabel={text('当前活动', 'Current activity')}
      />
    </div>
  );
}
