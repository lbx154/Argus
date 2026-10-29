/**
 * The 3D viewer for robot (URDF) and mesh results. Loaded lazily: three.js
 * and the loaders stay out of the main bundle until a model is opened.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box, Camera, Maximize, TriangleAlert } from 'lucide-react';
import { Box3, Vector3, type Object3D } from 'three';
import { useI18n } from '../../i18n';
import { downloadBlob } from '../../lib/downloadBlob';
import { jointControl, modelFormat, sweepValue, type JointControl } from '../../lib/modelFiles';
import { JointPanel } from './JointPanel';
import { loadModel, MISSING_FILE, type LoadedModel } from './loadModel';
import { disposeObject, ModelStage } from './ModelStage';
import { ErrorMessage, ErrorDetails } from '../ErrorMessage';
import './model.css';

export interface ModelViewerProps {
  sid: string;
  path: string;
  mode?: 'full' | 'thumbnail';
  /** Files next to a URDF that close a loop the URDF tree cannot hold. */
  constraintFiles?: string[];
  /** Thumbnail only: open the full viewer. */
  onOpen?: () => void;
  label?: string;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string; failures: LoadedModel['failures'] }
  | { status: 'ready'; model: LoadedModel & { dispose(): void } };

function useModel(sid: string, path: string, enabled: boolean) {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let loaded: (LoadedModel & { dispose(): void }) | null = null;
    setState({ status: 'loading' });
    loadModel(sid, path, controller.signal).then((model) => {
      if (controller.signal.aborted) { disposeObject(model.root); model.dispose(); return; }
      loaded = model;
      setState(model.shapes ? { status: 'ready', model } : { status: 'error', message: '', failures: model.failures });
    }, (error: Error) => {
      if (!controller.signal.aborted) setState({ status: 'error', message: error.message || String(error), failures: [] });
    });
    return () => {
      controller.abort();
      if (loaded) { disposeObject(loaded.root); loaded.dispose(); }
    };
  }, [sid, path, enabled]);
  return state;
}

function useTheme(onChange: () => void) {
  const latest = useRef(onChange);
  latest.current = onChange;
  useEffect(() => {
    const observer = new MutationObserver(() => latest.current());
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
    return () => observer.disconnect();
  }, []);
}

function ErrorCard({ message, failures, zh, compact }: { message: string; failures: LoadedModel['failures']; zh: boolean; compact?: boolean }) {
  if (compact) return <div className="model-thumb-error"><TriangleAlert size={14} aria-hidden="true" />{zh ? '3D 预览不可用，点击查看原因' : '3D preview unavailable — open for details'}</div>;
  return <div className="model-error" role="alert">
    <TriangleAlert size={20} aria-hidden="true" />
    <h3>{zh ? '这个模型无法显示' : 'This model could not be shown'}</h3>
    {message ? <div><ErrorMessage error={message} code="preview_failed" /></div> : null}
    {failures.length ? <>
      <p>{zh ? `模型引用的 ${failures.length} 个文件没能加载：` : `${failures.length} file(s) the model references could not be loaded:`}</p>
      <ul>{failures.map((failure) => <li key={failure.ref}><code>{failure.ref}</code><span>{failureText(failure.reason, zh)}</span><ErrorDetails error={failure.reason} /></li>)}</ul>
    </> : null}
    {!message && !failures.length ? <p>{zh ? '模型里没有可显示的几何体。' : 'The model contains no geometry to show.'}</p> : null}
    {failures.length ? <p className="model-error-hint">{zh ? '网格文件需要放在模型文件旁边（相对路径或 package:// 路径），并且在项目工作区内。' : 'Mesh files must sit beside the model (relative or package:// paths) inside the project workspace.'}</p> : null}
  </div>;
}

const failureText = (reason: string, zh: boolean) => reason === MISSING_FILE
  ? (zh ? '模型旁边没有这个文件，请检查引用路径。' : 'This file is missing beside the model. Check its reference path.')
  : (zh ? '文件没能加载。请检查文件内容和引用路径。' : 'The file could not be loaded. Check its contents and reference path.');

function formatSize(meters: number) {
  return meters >= 1 ? `${meters.toFixed(2)} m` : `${Math.round(meters * 1000)} mm`;
}

export default function ModelViewer({ sid, path, mode = 'full', constraintFiles = [], onOpen, label }: ModelViewerProps) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const thumbnail = mode === 'thumbnail';
  const frame = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const stage = useRef<ModelStage | null>(null);
  // A card loads its model only once it is on screen; the full viewer loads at once.
  const [seen, setSeen] = useState(!thumbnail);
  const [visible, setVisible] = useState(!thumbnail);
  const state = useModel(sid, path, seen);
  const model = state.status === 'ready' ? state.model : null;
  const [hover, setHover] = useState<{ name: string; x: number; y: number } | null>(null);
  const [geometry, setGeometry] = useState<'visual' | 'collision'>('visual');
  const [values, setValues] = useState<Record<string, number>>({});
  const [sweeping, setSweeping] = useState(false);
  const [panelOpen, setPanelOpen] = useState(() => typeof window === 'undefined' || window.innerWidth >= 640);
  const [notice, setNotice] = useState(true);
  const [size, setSize] = useState<{ x: number; y: number; z: number } | null>(null);

  const controls = useMemo<JointControl[]>(() => (model?.summary?.joints ?? [])
    .map(jointControl).filter((control): control is JointControl => control !== null), [model]);
  const home = useMemo(() => Object.fromEntries(controls.map((control) => [control.name, control.home])), [controls]);
  const readValues = useCallback(() => {
    const joints = model?.robot?.joints ?? {};
    return Object.fromEntries(controls.map((control) => [control.name, Number(joints[control.name]?.angle ?? control.home)]));
  }, [model, controls]);

  useEffect(() => {
    if (!thumbnail || !frame.current) return;
    const observer = new IntersectionObserver(([entry]) => {
      setVisible(entry.isIntersecting);
      if (entry.isIntersecting) setSeen(true);
    }, { rootMargin: '120px' });
    observer.observe(frame.current);
    return () => observer.disconnect();
  }, [thumbnail]);

  // Pose first, so the camera frames the robot as it is first shown.
  useEffect(() => {
    if (!model?.robot) return;
    model.robot.setJointValues(home);
    setValues(readValues());
    stage.current?.invalidate();
  }, [model, home, readValues]);

  // The stage exists while there is a model and (for a card) while it is on screen,
  // so a long conversation never holds more WebGL contexts than it shows.
  useEffect(() => {
    if (!model || !visible || !host.current) return;
    const created = new ModelStage(host.current, {
      thumbnail,
      zUp: model.format === 'urdf' || model.format === 'stl',
      onHover: thumbnail ? undefined : setHover,
    });
    stage.current = created;
    // Width × depth × height in the model's own frame, measured before staging.
    model.root.updateMatrixWorld(true);
    const measured = new Box3().setFromObject(model.root, true).getSize(new Vector3());
    setSize(model.format === 'urdf' || model.format === 'stl' ? measured : { x: measured.x, y: measured.z, z: measured.y });
    created.setModel(model.root as Object3D, model.robot);
    return () => {
      created.dispose();
      if (stage.current === created) stage.current = null;
    };
  }, [model, visible, thumbnail]);

  useEffect(() => stage.current?.setGeometry(geometry), [geometry, model, visible]);
  useTheme(() => stage.current?.applyTheme());

  useEffect(() => {
    if (!sweeping || !model?.robot) return;
    const start = performance.now();
    const from = readValues();
    let frameId = requestAnimationFrame(function step(now) {
      const seconds = (now - start) / 1000;
      const next: Record<string, number> = {};
      for (const control of controls) {
        if (!control.mimic && control.max > control.min) next[control.name] = sweepValue(control, seconds, from[control.name]);
      }
      stage.current?.setJoints(next);
      setValues(readValues());
      frameId = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(frameId);
  }, [sweeping, model, controls, readValues]);

  const setJoint = (name: string, value: number) => {
    setSweeping(false);
    stage.current?.setJoint(name, value);
    setValues(readValues());
  };
  const reset = () => {
    setSweeping(false);
    stage.current?.setJoints(home);
    setValues(readValues());
  };
  const screenshot = async () => {
    const blob = await stage.current?.screenshot();
    if (blob) downloadBlob(blob, `${(path.split('/').pop() || 'model').replace(/\.[^.]+$/, '')}.png`);
  };

  const name = model?.summary?.name || path.split('/').pop() || path;
  const format = modelFormat(path)?.toUpperCase() ?? '';
  const hasCollision = Boolean(model?.summary?.hasCollision && model.summary.hasVisual);
  const closure = constraintFiles.length > 0;
  const subtitle = closure
    ? (zh
      ? `这个机构的闭环写在 ${constraintFiles.join('、')} 里，URDF 本身只是一棵树。单独拖动一个关节，闭环会在画面上断开；这里不求解闭环，只显示 URDF 声明的运动。`
      : `This mechanism closes its loop in ${constraintFiles.join(', ')}; the URDF itself is a tree. Moving one joint alone visibly breaks the loop — the viewer does not solve the closure, it shows only what the URDF declares.`)
    : (zh ? '拖动滑块查看每个关节的运动范围；范围取自 URDF 的 limit。' : 'Drag a slider to move a joint; ranges come from the URDF limits.');

  if (thumbnail) {
    return <div ref={frame} className="model-thumb" data-model-thumbnail={path}>
      <button type="button" className="model-thumb-hit" onClick={onOpen} aria-label={zh ? `打开 3D 模型：${label || name}` : `Open 3D model: ${label || name}`}>
        <div ref={host} className="model-thumb-canvas" aria-hidden="true" />
        {state.status === 'loading' && seen ? <span className="model-thumb-status">{zh ? '正在载入 3D 模型…' : 'Loading 3D model…'}</span> : null}
        {state.status === 'error' ? <ErrorCard message={state.message} failures={state.failures} zh={zh} compact /> : null}
        <span className="model-thumb-badge"><Box size={12} aria-hidden="true" />{format === 'URDF' ? (zh ? '机器人模型' : 'Robot model') : `3D · ${format}`}</span>
        {state.status === 'ready' ? <span className="model-thumb-open">{zh ? '点击打开，可旋转和拖动关节' : 'Open to orbit and move joints'}</span> : null}
      </button>
    </div>;
  }

  if (state.status === 'error') return <ErrorCard message={state.message} failures={state.failures} zh={zh} />;

  return <div className="model-viewer" data-model-viewer={path}>
    <div className="model-stage">
      <div ref={host} className="model-stage-canvas" />
      {state.status === 'loading' ? <div className="model-stage-status" role="status"><span className="model-spinner" aria-hidden="true" />{zh ? '正在载入模型和网格…' : 'Loading the model and its meshes…'}</div> : null}
      {model ? <>
        <div className="model-chips">
          <span className="model-chip model-chip-strong"><Box size={13} aria-hidden="true" />{name}</span>
          {model.summary ? <span className="model-chip">{zh ? `${model.summary.links.length} 个连杆 · ${controls.length} 个可动关节` : `${model.summary.links.length} links · ${controls.length} movable joints`}</span> : <span className="model-chip">{format}</span>}
          {size ? <span className="model-chip">{formatSize(size.x)} × {formatSize(size.y)} × {formatSize(size.z)}</span> : null}
        </div>
        <div className="model-tools">
          {hasCollision ? <div className="model-segment" role="group" aria-label={zh ? '显示几何' : 'Geometry'}>
            <button type="button" aria-pressed={geometry === 'visual'} onClick={() => setGeometry('visual')}>{zh ? '外观' : 'Visual'}</button>
            <button type="button" aria-pressed={geometry === 'collision'} onClick={() => setGeometry('collision')}>{zh ? '碰撞体' : 'Collision'}</button>
          </div> : null}
          <button type="button" className="model-tool" onClick={() => stage.current?.fit()} title={zh ? '让模型充满视野' : 'Fit to view'} aria-label={zh ? '让模型充满视野' : 'Fit to view'}><Maximize size={15} /></button>
          <button type="button" className="model-tool" onClick={() => void screenshot()} title={zh ? '保存当前画面为 PNG' : 'Save view as PNG'} aria-label={zh ? '保存当前画面为 PNG' : 'Save view as PNG'}><Camera size={15} /></button>
        </div>
        {model.failures.length && notice ? <div className="model-notice" role="status">
          <TriangleAlert size={14} aria-hidden="true" />
          <div><strong>{zh ? `有 ${model.failures.length} 个网格没能加载，画面不完整：` : `${model.failures.length} mesh(es) failed to load; the view is incomplete:`}</strong>
            <ul>{model.failures.slice(0, 6).map((failure) => <li key={failure.ref}><code>{failure.ref}</code> — {failureText(failure.reason, zh)}<ErrorDetails error={failure.reason} /></li>)}</ul></div>
          <button type="button" onClick={() => setNotice(false)} aria-label={zh ? '关闭提示' : 'Dismiss'}>×</button>
        </div> : null}
        {hover ? <div className="model-hover" style={{ left: hover.x, top: hover.y }}>{hover.name}</div> : null}
        <p className="model-hint">{zh ? '拖动旋转 · 滚轮或双指缩放 · 右键或双指拖动平移' : 'Drag to orbit · scroll or pinch to zoom · right-drag or two fingers to pan'}</p>
      </> : null}
    </div>
    {model?.robot ? <JointPanel
      controls={controls} values={values} onChange={setJoint} onReset={reset}
      sweeping={sweeping} onToggleSweep={() => setSweeping((value) => !value)}
      subtitle={subtitle} caution={closure} open={panelOpen} onToggleOpen={() => setPanelOpen((value) => !value)} zh={zh}
    /> : null}
  </div>;
}
