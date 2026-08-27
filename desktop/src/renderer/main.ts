import './style.css';
import { captureIpc } from './ipcRecovery';

type LaunchState = 'idle' | 'starting' | 'ready' | 'error' | 'stopped';
type RunnerKind =
  | 'codex'
  | 'claude'
  | 'copilot'
  | 'pi'
  | 'opencode'
  | 'grok'
  | 'qoder'
  | 'dsh';
type AppearanceTheme = 'system' | 'light' | 'dark';

const RUNNER_LABELS: Record<RunnerKind, string> = {
  codex: 'Codex CLI',
  claude: 'Claude Code',
  copilot: 'Copilot CLI',
  pi: 'Pi（跟随用户模型）',
  opencode: 'OpenCode',
  grok: 'Grok Build',
  qoder: 'Qoder CLI',
  dsh: 'DeepSeek Harness'
};

function isRunnerKind(value: string | undefined): value is RunnerKind {
  return value !== undefined && Object.hasOwn(RUNNER_LABELS, value);
}

interface DesktopStatus {
  state: LaunchState;
  message: string;
  detail?: string;
  pid?: number;
  url?: string;
}

interface PiConfiguration {
  configDir: string;
  provider?: string;
  model?: string;
  qualifiedModel?: string;
}

interface DesktopReleaseIdentity {
  packageVersion: string;
  releaseId: string;
  sourceDigest: string;
  distribution: 'development' | 'packaged';
}

interface DesktopRuntimeIdentity {
  state: LaunchState;
  pid?: number;
  url?: string;
}

interface SetupInfo {
  complete: boolean;
  host: string;
  port: number;
  runnerKind: RunnerKind;
  runnerBins: Partial<Record<RunnerKind, string>>;
  runnerConfigured: boolean;
  detectedRunners: Partial<Record<RunnerKind, string>>;
  piConfiguration: PiConfiguration;
  releaseIdentity: DesktopReleaseIdentity;
  runtimeIdentity: DesktopRuntimeIdentity;
}

interface SetupResult {
  ok: boolean;
  error?: string;
}

interface DesktopAppearance {
  theme: AppearanceTheme;
  resolvedTheme: 'light' | 'dark';
}

declare global {
  interface Window {
    argusDesktop: {
      getStatus(): Promise<DesktopStatus>;
      onStatus(callback: (status: DesktopStatus) => void): () => void;
      getSetup(): Promise<SetupInfo>;
      getAppearance(): Promise<DesktopAppearance>;
      setAppearance(input: { theme: 'light' | 'dark' }): Promise<DesktopAppearance>;
      closeSetup(): Promise<void>;
      chooseRunner(kind: RunnerKind): Promise<string | null>;
      completeSetup(input: {
        port: number;
        runnerKind: RunnerKind;
        runnerBins: Partial<Record<RunnerKind, string>>;
      }): Promise<SetupResult>;
      openLogs(): Promise<string>;
      openData(): Promise<string>;
      restartBackend(): Promise<boolean>;
      exportDiagnostics(): Promise<string | null>;
      openCockpit(): Promise<void>;
      onShowSetup(callback: () => void): () => void;
      onNewChat(callback: () => void): () => void;
      quit(): Promise<void>;
    };
  }
}

const splashEl = document.getElementById('splash') as HTMLElement;
const statusEl = document.getElementById('status') as HTMLParagraphElement;
const detailEl = document.getElementById('detail') as HTMLParagraphElement;
const barEl = document.getElementById('bar') as HTMLSpanElement;
const retryEl = document.getElementById('retry') as HTMLButtonElement;
const setupEl = document.getElementById('setup') as HTMLButtonElement;
const diagnosticsEl = document.getElementById('diagnostics') as HTMLButtonElement;
const stepsEl = document.getElementById('steps') as HTMLOListElement;

const wizardEl = document.getElementById('wizard') as HTMLElement;
const wizardCaption = document.getElementById('wizardCaption') as HTMLParagraphElement;
const stepperEl = document.getElementById('stepper') as HTMLOListElement;
const panels = Array.from(document.querySelectorAll<HTMLElement>('.panel'));
const runnerStatus = document.getElementById('runnerStatus') as HTMLElement;
const runnerPath = document.getElementById('runnerPath') as HTMLElement;
const chooseRunnerEl = document.getElementById('chooseRunner') as HTMLButtonElement;
const chooseRunnerLabel = document.getElementById('chooseRunnerLabel') as HTMLSpanElement;
const clearRunnerEl = document.getElementById('clearRunner') as HTMLButtonElement;
const runnerKindSegmented = document.getElementById('runnerKindSegmented') as HTMLElement;
const runnerKindButtons = Array.from(
  runnerKindSegmented.querySelectorAll<HTMLButtonElement>('button')
);
const portInput = document.getElementById('portInput') as HTMLInputElement;
const portError = document.getElementById('portError') as HTMLParagraphElement;
const summaryRunner = document.getElementById('summaryRunner') as HTMLElement;
const summaryUrl = document.getElementById('summaryUrl') as HTMLElement;
const summaryRelease = document.getElementById('summaryRelease') as HTMLElement;
const summaryRuntime = document.getElementById('summaryRuntime') as HTMLElement;
const wizardCancel = document.getElementById('wizardCancel') as HTMLButtonElement;
const wizardBack = document.getElementById('wizardBack') as HTMLButtonElement;
const wizardNext = document.getElementById('wizardNext') as HTMLButtonElement;
const wizardFinish = document.getElementById('wizardFinish') as HTMLButtonElement;

const STEP_LABELS = ['Agent CLI', '本地服务', '确认设置'];

let cockpitOpening = false;
let wizardOpen = false;
let wizardPending = false;
let applying = false;
let currentStep = 0;
let setupRequested = false;
let runnerKind: RunnerKind = 'codex';
let runnerBins: Partial<Record<RunnerKind, string>> = {};
let detectedRunners: Partial<Record<RunnerKind, string>> = {};
let piConfiguration: PiConfiguration = { configDir: '' };
let releaseIdentity: DesktopReleaseIdentity = {
  packageVersion: 'unknown',
  releaseId: 'unknown',
  sourceDigest: '',
  distribution: 'development'
};
let runtimeIdentity: DesktopRuntimeIdentity = { state: 'idle' };
let port = 8799;
let appearanceTheme: AppearanceTheme = 'system';
const settingsOnly = new URLSearchParams(window.location.search).get('mode') === 'settings';
if (settingsOnly) document.documentElement.dataset.settingsMode = 'true';

function applyTheme(resolved?: 'light' | 'dark'): void {
  const dark = resolved
    ? resolved === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}

async function loadAppearance(): Promise<void> {
  const result = await captureIpc(() => window.argusDesktop.getAppearance());
  if (!result.ok) return;
  const appearance = result.value;
  appearanceTheme = appearance.theme;
  applyTheme(appearance.resolvedTheme);
}

window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (appearanceTheme === 'system') applyTheme();
});
applyTheme();

function activeStepEl(step: string): HTMLElement | null {
  return stepsEl.querySelector<HTMLElement>(`.step[data-step="${step}"]`);
}

function markStep(step: string, mode: 'active' | 'done' | 'error'): void {
  const el = activeStepEl(step);
  if (!el) return;
  el.classList.toggle('is-active', mode === 'active');
  el.classList.toggle('is-done', mode === 'done');
  el.classList.toggle('is-error', mode === 'error');
}

function resetSteps(): void {
  for (const el of Array.from(stepsEl.querySelectorAll<HTMLElement>('.step'))) {
    el.classList.remove('is-active', 'is-done', 'is-error');
  }
}

function updateSteps(status: DesktopStatus): void {
  resetSteps();
  if (status.state === 'ready') {
    markStep('env', 'done');
    markStep('service', 'done');
    markStep('workspace', 'done');
    return;
  }
  if (status.state === 'error' || status.state === 'stopped') {
    markStep('env', 'active');
    return;
  }
  const spawning = status.message.includes('启动');
  if (spawning) {
    markStep('env', 'done');
    markStep('service', 'active');
  } else {
    markStep('env', 'active');
  }
}

function render(status: DesktopStatus): void {
  document.body.dataset.state = status.state;
  statusEl.textContent = status.message;
  detailEl.hidden = !(status.state === 'error' && status.detail);
  if (status.detail) detailEl.textContent = status.detail;
  retryEl.hidden = status.state !== 'error';
  setupEl.hidden = status.state !== 'error';
  diagnosticsEl.hidden = status.state !== 'error';
  updateSteps(status);

  let width = 16;
  if (status.state === 'ready' || status.state === 'error') {
    width = 100;
  } else if (status.state === 'starting') {
    width = status.message.includes('启动') ? 68 : 38;
  }
  barEl.style.width = `${width}%`;

  if (status.state === 'ready') void handleReady();
}

function renderIpcFailure(message: string, detail: string): void {
  wizardPending = false;
  cockpitOpening = false;
  applying = false;
  splashEl.classList.remove('is-ready');
  render({ state: 'error', message, detail });
}

async function handleReady(): Promise<void> {
  if (cockpitOpening || wizardOpen || applying || wizardPending) return;
  wizardPending = true;
  const setupResult = await captureIpc(() => window.argusDesktop.getSetup());
  if (!setupResult.ok) {
    renderIpcFailure('无法读取桌面设置', setupResult.detail);
    return;
  }
  const setup = setupResult.value;
  port = setup.port;
  runnerKind = setup.runnerKind;
  runnerBins = { ...(setup.runnerBins || {}) };
  detectedRunners = { ...(setup.detectedRunners || {}) };
  piConfiguration = { ...(setup.piConfiguration || { configDir: '' }) };
  releaseIdentity = setup.releaseIdentity;
  runtimeIdentity = setup.runtimeIdentity;
  if (!setup.complete || !setup.runnerConfigured) {
    window.setTimeout(() => {
      wizardPending = false;
      showWizard(setup);
    }, 460);
    return;
  }
  wizardPending = false;
  if (setupRequested) return;
  cockpitOpening = true;
  splashEl.classList.add('is-ready');
  window.setTimeout(() => {
    if (wizardOpen || setupRequested) return;
    void captureIpc(() => window.argusDesktop.openCockpit()).then((result) => {
      if (!result.ok) renderIpcFailure('无法打开 Argus 工作台', result.detail);
    });
  }, 720);
}

function runnerDescription(path: string): string {
  if (runnerKind !== 'pi') return path;
  const model = piConfiguration.qualifiedModel || 'Pi 当前默认模型（未在 settings.json 中固定）';
  return `${path}\n模型：${model}`;
}

function renderRunner(): void {
  const manual = (runnerBins[runnerKind] || '').trim();
  const detected = (detectedRunners[runnerKind] || '').trim();
  if (manual) {
    runnerStatus.dataset.state = 'ok';
    runnerStatus.textContent = '已选择';
    runnerPath.textContent = runnerDescription(manual);
    clearRunnerEl.hidden = false;
  } else if (detected) {
    runnerStatus.dataset.state = 'ok';
    runnerStatus.textContent = '已自动检测';
    runnerPath.textContent = runnerDescription(detected);
    clearRunnerEl.hidden = true;
  } else {
    runnerStatus.dataset.state = 'warn';
    runnerStatus.textContent = '未检测到';
    runnerPath.textContent = `未找到 ${RUNNER_LABELS[runnerKind]}，可手动选择。`;
    clearRunnerEl.hidden = true;
  }
  chooseRunnerLabel.textContent = `选择 ${RUNNER_LABELS[runnerKind]}`;
}

function renderRunnerKind(): void {
  for (const button of runnerKindButtons) {
    button.classList.toggle('is-selected', button.dataset.kind === runnerKind);
  }
}

function isPortValid(): boolean {
  const value = Number(portInput.value);
  return Number.isInteger(value) && value >= 1024 && value <= 65535;
}

function renderSummary(): void {
  const path = (runnerBins[runnerKind] || detectedRunners[runnerKind] || '').trim();
  const model = runnerKind === 'pi' && piConfiguration.qualifiedModel
    ? ` · ${piConfiguration.qualifiedModel}`
    : '';
  summaryRunner.textContent = path
    ? `${RUNNER_LABELS[runnerKind]} · ${path}${model}`
    : `${RUNNER_LABELS[runnerKind]}（未配置）`;
  summaryUrl.textContent = `127.0.0.1:${port}`;
  summaryRelease.textContent = `${releaseIdentity.packageVersion} · ${releaseIdentity.distribution}`;
  summaryRuntime.textContent = runtimeIdentity.pid
    ? `${runtimeIdentity.state} · PID ${runtimeIdentity.pid}${runtimeIdentity.url ? ` · ${runtimeIdentity.url}` : ''}`
    : `${runtimeIdentity.state} · 后端将在保存后启动`;
}

function goToStep(step: number): void {
  currentStep = step;
  for (const panel of panels) {
    panel.hidden = panel.dataset.panel !== ['env', 'port', 'done'][step];
  }
  for (const item of Array.from(stepperEl.querySelectorAll<HTMLElement>('li'))) {
    const index = Number(item.dataset.step);
    item.classList.toggle('is-active', index === step);
    item.classList.toggle('is-done', index < step);
  }
  wizardBack.hidden = step === 0;
  wizardNext.hidden = step === 2;
  wizardFinish.hidden = step !== 2;
  wizardCaption.textContent = STEP_LABELS[step];
  if (step === 2) renderSummary();
}

function showWizard(setup: SetupInfo): void {
  wizardOpen = true;
  wizardFinish.disabled = false;
  wizardEl.hidden = false;
  splashEl.classList.add('has-wizard');
  runnerKind = setup.runnerKind;
  runnerBins = { ...(setup.runnerBins || {}) };
  detectedRunners = { ...(setup.detectedRunners || {}) };
  piConfiguration = { ...(setup.piConfiguration || { configDir: '' }) };
  releaseIdentity = setup.releaseIdentity;
  runtimeIdentity = setup.runtimeIdentity;
  port = setup.port;
  portInput.value = String(port);
  renderRunnerKind();
  renderRunner();
  goToStep(0);
}

async function reopenWizard(): Promise<void> {
  const result = await captureIpc(() => window.argusDesktop.getSetup());
  if (!result.ok) {
    renderIpcFailure('无法读取桌面设置', result.detail);
    return;
  }
  showWizard(result.value);
}

retryEl.addEventListener('click', () => {
  retryEl.disabled = true;
  statusEl.textContent = '正在重新启动 Argus';
  void captureIpc(() => window.argusDesktop.restartBackend()).then((result) => {
    if (!result.ok) renderIpcFailure('无法重新启动 Argus', result.detail);
  }).finally(() => {
    retryEl.disabled = false;
  });
});

setupEl.addEventListener('click', () => {
  void reopenWizard();
});

diagnosticsEl.addEventListener('click', () => {
  diagnosticsEl.disabled = true;
  void captureIpc(() => window.argusDesktop.exportDiagnostics()).then((result) => {
    if (!result.ok) {
      renderIpcFailure('无法导出诊断', result.detail);
      return;
    }
    if (!result.value) return;
    detailEl.hidden = false;
    detailEl.textContent = `脱敏诊断已导出：${result.value}`;
  }).finally(() => {
    diagnosticsEl.disabled = false;
  });
});

chooseRunnerEl.addEventListener('click', async () => {
  chooseRunnerEl.disabled = true;
  const result = await captureIpc(() => window.argusDesktop.chooseRunner(runnerKind));
  chooseRunnerEl.disabled = false;
  if (!result.ok) {
    runnerStatus.dataset.state = 'warn';
    runnerStatus.textContent = '选择失败';
    runnerPath.textContent = result.detail;
    return;
  }
  const path = result.value;
  if (path) {
    runnerBins[runnerKind] = path;
    renderRunner();
  }
});

clearRunnerEl.addEventListener('click', () => {
  delete runnerBins[runnerKind];
  renderRunner();
});

for (const button of runnerKindButtons) {
  button.addEventListener('click', () => {
    const kind = button.dataset.kind;
    if (isRunnerKind(kind)) runnerKind = kind;
    renderRunnerKind();
    renderRunner();
  });
}

portInput.addEventListener('input', () => {
  portError.hidden = isPortValid();
});

wizardCancel.addEventListener('click', () => {
  if (!applying && settingsOnly) void window.argusDesktop.closeSetup();
});

wizardBack.addEventListener('click', () => {
  if (currentStep > 0) goToStep(currentStep - 1);
});

wizardNext.addEventListener('click', () => {
  if (currentStep === 0) {
    goToStep(1);
    return;
  }
  if (currentStep === 1) {
    if (!isPortValid()) {
      portError.hidden = false;
      portInput.focus();
      return;
    }
    goToStep(2);
  }
});

wizardFinish.addEventListener('click', async () => {
  if (!isPortValid()) {
    goToStep(1);
    portError.hidden = false;
    portInput.focus();
    return;
  }
  port = Number(portInput.value);
  wizardFinish.disabled = true;
  applying = true;
  setupRequested = false;
  wizardOpen = false;
  wizardEl.hidden = true;
  splashEl.classList.remove('has-wizard');
  document.body.dataset.state = 'starting';
  statusEl.textContent = '正在应用设置';
  detailEl.hidden = true;
  retryEl.hidden = true;
  setupEl.hidden = true;
  diagnosticsEl.hidden = true;
  barEl.style.width = '72%';

  const invocation = await captureIpc(() => window.argusDesktop.completeSetup({
    port,
    runnerKind,
    runnerBins
  }));
  if (!invocation.ok || !invocation.value.ok) {
    applying = false;
    document.body.dataset.state = 'error';
    statusEl.textContent = '设置保存失败';
    detailEl.textContent = invocation.ok
      ? (invocation.value.error || '未知错误')
      : invocation.detail;
    detailEl.hidden = false;
    retryEl.hidden = false;
    setupEl.hidden = false;
    diagnosticsEl.hidden = false;
    return;
  }
  applying = false;
  if (settingsOnly) {
    await window.argusDesktop.closeSetup();
    return;
  }
  window.setTimeout(() => {
    void captureIpc(() => window.argusDesktop.getStatus()).then((status) => {
      if (status.ok) render(status.value);
      else renderIpcFailure('无法读取本地服务状态', status.detail);
    });
  }, 260);
});

window.addEventListener('keydown', (event) => {
  if (settingsOnly && wizardOpen && !applying && event.key === 'Escape') {
    event.preventDefault();
    void window.argusDesktop.closeSetup();
  }
});

window.argusDesktop.onNewChat(() => window.postMessage({ type: 'argus:new-chat' }, '*'));

window.argusDesktop.onShowSetup(() => {
  setupRequested = true;
  void reopenWizard();
});

void loadAppearance();
if (settingsOnly) {
  setupRequested = true;
  wizardCancel.hidden = false;
  void reopenWizard();
} else {
  void captureIpc(() => window.argusDesktop.getStatus()).then((status) => {
    if (status.ok) render(status.value);
    else renderIpcFailure('无法读取本地服务状态', status.detail);
  });
  window.argusDesktop.onStatus(render);
}
