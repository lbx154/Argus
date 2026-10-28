import { ChevronDown, Pause, Play, RotateCcw } from 'lucide-react';
import { formatJoint, fromDisplay, toDisplay, type JointControl } from '../../lib/modelFiles';

/** Sliders for every movable URDF joint, in the file's order, bounded by its limits. */
export function JointPanel({
  controls, values, onChange, onReset, sweeping, onToggleSweep, subtitle, caution = false, open, onToggleOpen, zh,
}: {
  controls: JointControl[];
  /** SI values by joint name. */
  values: Record<string, number>;
  onChange: (name: string, value: number) => void;
  onReset: () => void;
  sweeping: boolean;
  onToggleSweep: () => void;
  subtitle: string;
  /** The subtitle warns about something the view cannot show (an external loop closure). */
  caution?: boolean;
  open: boolean;
  onToggleOpen: () => void;
  zh: boolean;
}) {
  const driven = controls.filter((control) => !control.mimic && control.max > control.min);
  return (
    <aside className="model-joints" data-open={open || undefined} aria-label={zh ? '关节' : 'Joints'}>
      <button type="button" className="model-joints-head" onClick={onToggleOpen} aria-expanded={open}>
        <span className="model-joints-title">{zh ? '关节' : 'Joints'}<small>{controls.length}</small></span>
        <ChevronDown size={16} className="model-joints-chevron" aria-hidden="true" />
      </button>
      <div className="model-joints-body">
        <p className="model-joints-subtitle" data-caution={caution || undefined}>{subtitle}</p>
        <div className="model-joints-actions">
          <button type="button" onClick={onReset}><RotateCcw size={14} aria-hidden="true" />{zh ? '回到初始姿态' : 'Reset pose'}</button>
          <button type="button" onClick={onToggleSweep} aria-pressed={sweeping} disabled={!driven.length}>
            {sweeping ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
            {sweeping ? (zh ? '停止扫动' : 'Stop sweep') : (zh ? '扫动全部关节' : 'Play sweep')}
          </button>
        </div>
        {controls.length ? <ul className="model-joints-list">
          {controls.map((control) => {
            const value = values[control.name] ?? control.home;
            const fixedRange = !(control.max > control.min);
            return <li key={control.name} data-joint={control.name}>
              <div className="model-joint-row">
                <span className="model-joint-name" title={control.name}>{control.name}</span>
                <span className="model-joint-value">{formatJoint(control, value)}</span>
              </div>
              <input
                type="range"
                min={control.min}
                max={control.max}
                step={control.step}
                value={toDisplay(control, value)}
                disabled={Boolean(control.mimic) || fixedRange}
                aria-label={`${control.name} (${control.unit === 'deg' ? (zh ? '度' : 'degrees') : (zh ? '毫米' : 'millimetres')})`}
                onChange={(event) => onChange(control.name, fromDisplay(control, Number(event.target.value)))}
              />
              <div className="model-joint-meta">
                <span>{control.type === 'prismatic' ? (zh ? '移动' : 'prismatic') : control.type === 'continuous' ? (zh ? '连续转动' : 'continuous') : (zh ? '转动' : 'revolute')}</span>
                <span>{control.mimic
                  ? (zh ? `跟随 ${control.mimic}` : `follows ${control.mimic}`)
                  : fixedRange ? (zh ? '未声明可动范围' : 'no range declared')
                    : `${control.min}${control.unit === 'deg' ? '°' : ''} … ${control.max}${control.unit === 'deg' ? '°' : ' mm'}`}</span>
              </div>
            </li>;
          })}
        </ul> : <p className="model-joints-empty">{zh ? '这个模型没有可动关节。' : 'This model has no movable joints.'}</p>}
      </div>
    </aside>
  );
}
