import type { ExecutionOption, TimelineTask } from './types';

const field = 'w-full rounded-lg border border-line bg-transparent px-2 py-1.5 text-sm';
export function ExecutionOptionsEditor({ task, resources, onChange, text }: {
  task: TimelineTask; resources: string[]; onChange: (options: ExecutionOption[]) => void;
  text: (zh: string, en: string) => string;
}) {
  const options = task.execution_options ?? [];
  const update = (id: string, patch: Partial<ExecutionOption>) => onChange(options.map((o) => o.id === id ? { ...o, ...patch } : o));
  return <div className="space-y-3 rounded-lg border border-dashed border-line p-3 sm:col-span-2">
    <h4 className="text-sm font-medium">{text('期限紧张时的替代做法', 'Alternative execution options for tighter deadlines')}</h4>
    <p className="text-xs text-ink-faint">{text('每种做法都有独立工期、资源和取舍。系统只会选择已声明保留必需验收目标的方案，不自动压缩原估计。', 'Each option has its own estimate, resources and tradeoff. Only options declared to preserve required acceptance scope can be selected.')}</p>
    {options.map((option) => <fieldset key={option.id} disabled={task.status != null && task.status !== 'pending'} className="grid gap-2 rounded-lg border border-line p-3 sm:grid-cols-2">
      <label className="text-xs sm:col-span-2">{text('替代做法', 'Alternative approach')}<input className={field} value={option.title} onChange={(e) => update(option.id, { title: e.target.value })} /></label>
      <div className="grid grid-cols-3 gap-2 sm:col-span-2">{[text('替代下限（小时）', 'Option lower (h)'), text('替代最可能（小时）', 'Option likely (h)'), text('替代上限（小时）', 'Option upper (h)')].map((label, index) => <label key={label} className="text-xs">{label}<input className={field} type="number" min="0" step="any" value={option.duration_hours[index]} onChange={(e) => {
        const duration: [number, number, number] = [...option.duration_hours]; duration[index] = Number(e.target.value); update(option.id, { duration_hours: duration });
      }} /></label>)}</div>
      {resources.map((name) => <label key={name} className="text-xs">{name} {text('需求', 'required')}<input className={field} type="number" min="0" step="1" value={option.resources[name] ?? 0} onChange={(e) => {
        const demand = { ...option.resources }; const count = Number(e.target.value); if (count) demand[name] = count; else delete demand[name]; update(option.id, { resources: demand });
      }} /></label>)}
      <label className="text-xs">{text('替代工期依据', 'Option estimate basis')}<textarea aria-label={text('替代工期依据', 'Option estimate basis')} className={field} value={option.basis} onChange={(e) => update(option.id, { basis: e.target.value })} /></label>
      <label className="text-xs">{text('取舍与适用条件', 'Tradeoff and prerequisites')}<textarea aria-label={text('取舍与适用条件', 'Tradeoff and prerequisites')} className={field} value={option.tradeoff} onChange={(e) => update(option.id, { tradeoff: e.target.value })} /></label>
      <label className="flex gap-2 text-xs sm:col-span-2"><input type="checkbox" checked={option.preserves_acceptance} onChange={(e) => update(option.id, { preserves_acceptance: e.target.checked })} />{text('确认条件适用且保留必需验收目标，允许重排时采用', 'Prerequisites apply and required acceptance is preserved; allow automatic selection')}</label>
      <button className="justify-self-start text-xs text-red" onClick={() => onChange(options.filter((o) => o.id !== option.id))}>{text('移除替代做法', 'Remove alternative')}</button>
    </fieldset>)}
    <button className="rounded-lg border border-line px-3 py-1.5 text-xs" disabled={options.length >= 8 || (task.status != null && task.status !== 'pending')} onClick={() => {
      let n = options.length + 1; while (options.some((o) => o.id === `option-${n}`)) n++;
      onChange([...options, { id: `option-${n}`, title: text('新的替代做法', 'New alternative'), duration_hours: [...task.duration_hours], resources: { ...task.resources }, basis: task.basis,
        tradeoff: text('待补充适用条件与取舍', 'Specify prerequisites and tradeoffs'), preserves_acceptance: false }]);
    }}>{text('添加替代做法', 'Add alternative')}</button>
  </div>;
}
