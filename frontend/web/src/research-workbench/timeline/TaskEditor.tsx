import type { TimelineInput, TimelineTask } from './types';

type Text = (zh: string, en: string) => string;
const inputClass = 'w-full rounded-lg border border-line bg-transparent px-2 py-1.5 text-sm text-ink';

export function TaskEditor({ input, onChange, text }: { input: TimelineInput; onChange: (value: TimelineInput) => void; text: Text }) {
  const proposal = input.proposals.find((item) => item.id === input.selected_proposal_id)!;
  const update = (id: string, patch: Partial<TimelineTask>) => onChange({ ...input, proposals: input.proposals.map((p) => p.id === proposal.id
    ? { ...p, tasks: p.tasks.map((task) => task.id === id ? { ...task, ...patch } : task) } : p) });
  const add = () => {
    let n = proposal.tasks.length + 1;
    while (proposal.tasks.some((task) => task.id === `task-${n}`)) n++;
    onChange({ ...input, proposals: input.proposals.map((p) => p.id === proposal.id ? { ...p, tasks: [...p.tasks, {
      id: `task-${n}`, title: text('新增实验', 'New experiment'), phase: 'experiment', duration_hours: [1, 2, 8],
      basis: text('初步估计，待实测校准', 'Preliminary estimate; needs calibration'),
    }] } : p) });
  };
  return <section className="space-y-3" aria-label={text('任务与实验', 'Tasks and experiments')}>
    <div className="flex items-center justify-between"><h3 className="font-medium">{text('任务与实验', 'Tasks and experiments')}</h3>
      <button className="rounded-lg border border-line px-3 py-1.5 text-sm" onClick={add}>{text('添加任务', 'Add task')}</button></div>
    {proposal.tasks.map((task) => {
      const executed = ['running', 'completed', 'failed'].includes(task.status ?? 'pending');
      const terminal = ['completed', 'failed'].includes(task.status ?? 'pending');
      return <details key={task.id} className="rounded-xl border border-line p-3">
        <summary className="cursor-pointer text-sm"><strong>{task.title}</strong><span className="ml-2 text-ink-faint">{task.duration_hours.join(' / ')} h · {task.id}</span></summary>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="text-xs">{text('任务名称', 'Task name')}<input className={inputClass} value={task.title} onChange={(e) => update(task.id, { title: e.target.value })} /></label>
          <label className="text-xs">{text('阶段', 'Phase')}<input className={inputClass} value={task.phase} onChange={(e) => update(task.id, { phase: e.target.value })} /></label>
          <div className="sm:col-span-2 grid grid-cols-3 gap-2">{[text('乐观（小时）', 'Lower (hours)'), text('最可能（小时）', 'Likely (hours)'), text('悲观（小时）', 'Upper (hours)')].map((label, i) =>
            <label key={label} className="text-xs">{label}<input type="number" min="0" step="any" className={inputClass} disabled={executed} value={task.duration_hours[i]} onChange={(e) => {
              const duration: [number, number, number] = [...task.duration_hours]; duration[i] = Number(e.target.value); update(task.id, { duration_hours: duration });
            }} /></label>)}</div>
          <label className="text-xs">{text('估计依据', 'Estimate basis')}<textarea className={inputClass} value={task.basis} onChange={(e) => update(task.id, { basis: e.target.value })} /></label>
          <label className="text-xs">{text('模型 / 实现难度', 'Model / implementation difficulty')}<textarea className={inputClass} value={task.difficulty ?? ''} onChange={(e) => update(task.id, { difficulty: e.target.value })} /></label>
          <label className="text-xs">{text('前置任务（可多选）', 'Dependencies (multiple)')}<select aria-label={text('前置任务（可多选）', 'Dependencies (multiple)')} multiple className={inputClass} disabled={executed} value={task.depends_on ?? []} onChange={(e) => update(task.id, { depends_on: Array.from(e.target.selectedOptions, (o) => o.value) })}>
            {proposal.tasks.filter((other) => other.id !== task.id).map((other) => <option value={other.id} key={other.id}>{other.title}</option>)}
          </select></label>
          <div className="grid grid-cols-2 gap-2">{Object.keys(input.resources).map((resource) => <label className="text-xs" key={resource}>{resource} {text('占用', 'slots')}<input type="number" min="0" step="1" className={inputClass} disabled={executed} value={task.resources?.[resource] ?? 0} onChange={(e) => {
            const resources = { ...task.resources }; const value = Number(e.target.value);
            if (value) resources[resource] = value; else delete resources[resource]; update(task.id, { resources });
          }} /></label>)}</div>
          <label className="text-xs">{text('进展状态', 'Progress status')}<select aria-label={text('进展状态', 'Progress status')} className={inputClass} disabled={terminal} value={task.status ?? 'pending'} onChange={(e) => {
            const status = e.target.value as TimelineTask['status'];
            update(task.id, { status, ...(status === 'running' ? { actual_start_hours: task.actual_start_hours ?? input.now_hours, remaining_hours: task.remaining_hours ?? [...task.duration_hours] } : {}),
              ...(['completed', 'failed'].includes(status!) ? { actual_start_hours: task.actual_start_hours ?? 0, actual_finish_hours: input.now_hours } : {}) });
          }}>
            {([['pending', '未开始', 'Pending'], ['running', '运行中', 'Running'], ['completed', '已完成', 'Completed'], ['failed', '失败', 'Failed'], ['blocked', '受阻', 'Blocked']] as const).filter(([value]) => !executed || !['pending', 'blocked'].includes(value)).map(([value, zh, en]) => <option key={value} value={value}>{text(zh, en)}</option>)}
          </select></label>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={task.optional ?? false} onChange={(e) => update(task.id, { optional: e.target.checked })} />{text('可选 / 已退出交付路径', 'Optional / retired from delivery path')}</label>
          {executed && <label className="text-xs">{text('实际开始（项目第几小时）', 'Actual start (project hour)')}<input type="number" min="0" step="any" className={inputClass} value={task.actual_start_hours ?? 0} onChange={(e) => update(task.id, { actual_start_hours: Number(e.target.value) })} /></label>}
          {terminal && <label className="text-xs">{text('实际结束（项目第几小时）', 'Actual finish (project hour)')}<input type="number" min="0" step="any" className={inputClass} value={task.actual_finish_hours ?? 0} onChange={(e) => update(task.id, { actual_finish_hours: Number(e.target.value) })} /></label>}
          {task.status === 'running' && <div className="sm:col-span-2 grid grid-cols-3 gap-2">{[text('剩余下限', 'Remaining lower'), text('剩余最可能', 'Remaining likely'), text('剩余上限', 'Remaining upper')].map((label, i) => <label key={label} className="text-xs">{label}<input type="number" min="0" step="any" className={inputClass} value={task.remaining_hours?.[i] ?? 0} onChange={(e) => {
            const remaining: [number, number, number] = [...(task.remaining_hours ?? task.duration_hours)]; remaining[i] = Number(e.target.value); update(task.id, { remaining_hours: remaining });
          }} /></label>)}</div>}
          <label className="text-xs">{text('变化 / 延期原因', 'Change / delay reason')}<textarea className={inputClass} value={task.reason ?? ''} onChange={(e) => update(task.id, { reason: e.target.value })} /></label>
          <label className="text-xs">{text('证据引用（每行一个）', 'Evidence references (one per line)')}<textarea className={inputClass} value={(task.evidence ?? []).join('\n')} onChange={(e) => update(task.id, { evidence: e.target.value.split('\n').filter(Boolean) })} /></label>
          {!executed && proposal.tasks.length > 1 && <button className="justify-self-start text-xs text-red" onClick={() => onChange({ ...input, proposals: input.proposals.map((p) => p.id === proposal.id ? { ...p, tasks: p.tasks.filter((t) => t.id !== task.id) } : p) })}>{text('移除未开始任务', 'Remove unstarted task')}</button>}
        </div>
      </details>;
    })}
  </section>;
}
