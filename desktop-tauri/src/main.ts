import './style.css';
import {
  desktopBridge,
  type AppearanceTheme,
  type DesktopAppearance,
  type DesktopReleaseIdentity,
  type DesktopRuntimeIdentity,
  type DesktopSetup,
  type DesktopStatus,
  type PiConfiguration,
  type RunnerKind,
  type TrialDownloadProgress,
  type UpdateStatus,
} from './bridge';

const RUNNER_LABELS: Record<RunnerKind, string> = {
  codex: 'Codex CLI',
  claude: 'Claude Code',
  copilot: 'Copilot CLI',
  cursor: 'Cursor CLI',
  pi: 'Pi（跟随用户模型）',
  opencode: 'OpenCode',
  grok: 'Grok Build',
  qoder: 'Qoder CLI',
  dsh: 'DeepSeek Harness',
};

function isRunnerKind(value: string | undefined): value is RunnerKind {
  return value !== undefined && Object.hasOwn(RUNNER_LABELS, value);
}

function errorDetail(error: unknown): string {
  return error instanceof Error ? `${error.name}: ${error.message}` : String(error);
}

async function capture<T>(operation: () => Promise<T>): Promise<
  | { ok: true; value: T }
  | { ok: false; detail: string }
> {
  try {
    return { ok: true, value: await operation() };
  } catch (error) {
    return { ok: false, detail: errorDetail(error) };
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
const wizardError = document.getElementById('wizardError') as HTMLParagraphElement;
const wizardBody = document.querySelector('.wizard-body') as HTMLElement;
const refreshRunners = document.getElementById('refreshRunners') as HTMLButtonElement;
const runtimeNotice = document.getElementById('runtimeNotice') as HTMLElement;
const runtimeNoticeDetail = document.getElementById('runtimeNoticeDetail') as HTMLElement;
new ResizeObserver(() => {
  document.documentElement.style.setProperty('--desktop-notice-height', `${runtimeNotice.hidden ? 0 : runtimeNotice.offsetHeight}px`);
}).observe(runtimeNotice);
const desktopContext = document.getElementById('desktopContext') as HTMLElement;
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
  runnerKindSegmented.querySelectorAll<HTMLButtonElement>('button'),
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
const trialOpen = document.getElementById('trialOpen') as HTMLButtonElement;
const trialForm = document.getElementById('trialForm') as HTMLFormElement;
const trialKey = document.getElementById('trialKey') as HTMLInputElement;
const trialSubmit = document.getElementById('trialSubmit') as HTMLButtonElement;
const trialProgress = document.getElementById('trialProgress') as HTMLParagraphElement;
const trialDownload = document.getElementById('trialDownload') as HTMLDivElement;
const trialDownloadBar = document.getElementById('trialDownloadBar') as HTMLProgressElement;
const trialDownloadDetails = document.getElementById('trialDownloadDetails') as HTMLParagraphElement;
const trialNetworkHint = document.getElementById('trialNetworkHint') as HTMLParagraphElement;
let trialDownloadTimer: ReturnType<typeof setTimeout> | undefined;
const trialShortcut = document.getElementById('trialShortcut') as HTMLButtonElement;
let trialBusy = false;

const cockpitEl = document.getElementById('cockpit') as HTMLElement;
const cockpitFrame = document.getElementById('cockpitFrame') as HTMLIFrameElement;
const updateEl = document.getElementById('updateNotice') as HTMLElement;
const updateKickerEl = document.getElementById('updateKicker') as HTMLElement;
const updateTitleEl = document.getElementById('updateTitle') as HTMLElement;
const updateDetailEl = document.getElementById('updateDetail') as HTMLElement;
const updateSecurityEl = document.getElementById('updateSecurity') as HTMLElement;
const updateInstallEl = document.getElementById('updateInstall') as HTMLButtonElement;
const updateCheckEl = document.getElementById('updateCheck') as HTMLButtonElement;
const updateDismissEl = document.getElementById('updateDismiss') as HTMLButtonElement;
const desktopMenuBar = document.getElementById('desktopMenuBar') as HTMLElement;
const fileMenuTrigger = document.getElementById('fileMenuTrigger') as HTMLButtonElement;
const helpMenuTrigger = document.getElementById('helpMenuTrigger') as HTMLButtonElement;
const fileMenu = document.getElementById('fileMenu') as HTMLElement;
const helpMenu = document.getElementById('helpMenu') as HTMLElement;
const desktopMenuActions = Array.from(
  document.querySelectorAll<HTMLButtonElement>('[data-menu-action]'),
);

const STEP_LABELS = ['Agent CLI', '本地服务', '确认设置'];

let cockpitOpening = false;
let cockpitMounted = false;
let wizardOpen = false;
let wizardPending = false;
let applying = false;
let currentStep = 0;
let setupRequested = false;
let runnerKind: RunnerKind = 'codex';
let runnerSelected = false;
let runnerBins: Partial<Record<RunnerKind, string>> = {};
let detectedRunners: Partial<Record<RunnerKind, string>> = {};
let piConfiguration: PiConfiguration = { configDir: '' };
let releaseIdentity: DesktopReleaseIdentity = {
  packageVersion: 'unknown',
  releaseId: 'unknown',
  sourceDigest: '',
  distribution: 'development',
};
let runtimeIdentity: DesktopRuntimeIdentity = { state: 'idle' };
let port = 8799;
let appearanceTheme: AppearanceTheme = 'system';
let cockpitTheme: 'light' | 'dark' | null = null;
let updateStatus: UpdateStatus | null = null;
let splashHideTimer: number | undefined;
let returnFocus: HTMLElement | null = null;

function resolvedSystemTheme(): 'light' | 'dark' {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(resolved?: 'light' | 'dark'): void {
  document.documentElement.dataset.theme = resolved || resolvedSystemTheme();
}

function currentResolvedTheme(): 'light' | 'dark' {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

async function loadAppearance(): Promise<void> {
  const result = await capture(() => desktopBridge.getAppearance());
  if (!result.ok) return;
  const appearance: DesktopAppearance = result.value;
  appearanceTheme = appearance.theme;
  const resolved = appearance.theme === 'system'
    ? resolvedSystemTheme()
    : appearance.resolvedTheme;
  applyTheme(resolved);
  void desktopBridge.setWindowTheme(resolved);
}

window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (appearanceTheme !== 'system' || cockpitTheme) return;
  const resolved = resolvedSystemTheme();
  applyTheme(resolved);
  void desktopBridge.setWindowTheme(resolved);
});
applyTheme();
document.documentElement.dataset.argusDesktop = 'true';
document.documentElement.dataset.argusDesktopNativeFrame = 'true';

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
  const spawning = status.message.includes('启动') || status.message.includes('恢复');
  if (spawning) {
    markStep('env', 'done');
    markStep('service', 'active');
  } else {
    markStep('env', 'active');
  }
}

function showLauncher(preserveCockpit = false): void {
  if (splashHideTimer !== undefined) {
    window.clearTimeout(splashHideTimer);
    splashHideTimer = undefined;
  }
  splashEl.hidden = false;
  if (!preserveCockpit) {
    cockpitMounted = false;
    cockpitFrame.removeAttribute('src');
  }
  cockpitOpening = false;
  cockpitEl.hidden = true;
  splashEl.classList.remove('is-ready');
  cockpitTheme = null;
  if (appearanceTheme === 'system') applyTheme(resolvedSystemTheme());
  document.documentElement.dataset.settingsMode = 'false';
  void desktopBridge.setWindowTheme(currentResolvedTheme());
}

function cockpitOrigin(): string | null {
  try {
    return cockpitFrame.src ? new URL(cockpitFrame.src).origin : null;
  } catch {
    return null;
  }
}

function postToCockpit(type: string, payload?: unknown): void {
  if (!cockpitMounted || !cockpitFrame.contentWindow) return;
  const origin = cockpitOrigin();
  if (!origin) return;
  cockpitFrame.contentWindow.postMessage({ type, payload }, origin);
}

function sameCockpitConnection(left: string, right: string): boolean {
  const normalize = (value: string) => {
    const url = new URL(value);
    url.searchParams.delete('desktopTheme');
    return url.toString();
  };
  return normalize(left) === normalize(right);
}

function mountCockpit(url: string): void {
  if (splashHideTimer !== undefined) {
    window.clearTimeout(splashHideTimer);
    splashHideTimer = undefined;
  }
  splashEl.hidden = false;
  cockpitOpening = false;
  wizardPending = false;
  cockpitEl.hidden = false;
  splashEl.classList.add('is-ready');

  // A runner-only settings change restarts the backend at the same URL. Keep
  // the already-mounted React cockpit alive so it can reconnect through its
  // normal WebSocket/query recovery instead of paying for a full WebView reload.
  if (cockpitMounted && sameCockpitConnection(cockpitFrame.src, url)) {
    hideSplashAfterCockpitLoad();
    return;
  }
  cockpitMounted = true;
  cockpitFrame.src = url;
}

function hideSplashAfterCockpitLoad(): void {
  if (!cockpitMounted) return;
  if (splashHideTimer !== undefined) window.clearTimeout(splashHideTimer);
  // Let the opacity-only exit complete before removing the launcher.
  splashHideTimer = window.setTimeout(() => {
    if (cockpitMounted && !wizardOpen) splashEl.hidden = true;
    splashHideTimer = undefined;
  }, 180);
}

function render(status: DesktopStatus): void {
  if (trialBusy) return;
  document.body.dataset.state = status.state;
  runtimeNotice.hidden = !status.warning;
  runtimeNoticeDetail.textContent = status.warning || '';
  statusEl.textContent = status.message;
  detailEl.hidden = !(status.state === 'error' && status.detail);
  if (status.detail) detailEl.textContent = status.detail;
  retryEl.hidden = status.state !== 'error';
  setupEl.hidden = status.state !== 'error';
  diagnosticsEl.hidden = status.state !== 'error';
  updateSteps(status);

  let width = 16;
  if (status.state === 'ready' || status.state === 'error') width = 100;
  else if (status.state === 'starting') width = status.message.includes('启动') ? 68 : 38;
  barEl.style.width = `${width}%`;

  const channel = releaseIdentity.distribution === 'preview' ? 'Preview · 独立数据' : '本地工作台';
  desktopContext.textContent = status.state === 'ready' ? channel : `${channel} · ${status.message}`;
  desktopContext.title = status.message;
  if (status.state === 'error' && !applying && !wizardOpen) showLauncher(true);
  if (status.state === 'ready' || status.state === 'idle') void handleReady();
}

function renderIpcFailure(message: string, detail: string): void {
  wizardPending = false;
  cockpitOpening = false;
  applying = false;
  setupRequested = false;
  showLauncher(true);
  render({ state: 'error', message, detail });
}

function applySetup(setup: DesktopSetup): void {
  port = setup.port;
  runnerKind = setup.runnerKind;
  runnerSelected = setup.runnerConfigured;
  runnerBins = { ...(setup.runnerBins || {}) };
  detectedRunners = { ...(setup.detectedRunners || {}) };
  piConfiguration = { ...(setup.piConfiguration || { configDir: '' }) };
  releaseIdentity = setup.releaseIdentity;
  runtimeIdentity = setup.runtimeIdentity;
  if (releaseIdentity.distribution === 'preview') desktopContext.textContent = 'Preview · 独立数据';
}

async function handleReady(): Promise<void> {
  if (cockpitOpening || (cockpitMounted && !cockpitEl.hidden) || wizardOpen || applying || wizardPending) return;
  if (setupRequested) return;
  cockpitOpening = true;
  const setup = await capture(() => desktopBridge.getSetup());
  if (!setup.ok) {
    renderIpcFailure('无法读取桌面设置', setup.detail);
    return;
  }
  if (wizardOpen || setupRequested) {
    cockpitOpening = false;
    return;
  }
  applySetup(setup.value);
  if (!setup.value.complete) {
    cockpitOpening = false;
    showWizard(setup.value);
    return;
  }
  const status = await capture(() => desktopBridge.getStatus());
  if (!status.ok) {
    renderIpcFailure('无法读取本地服务状态', status.detail);
    return;
  }
  if (status.value.state !== 'ready') {
    cockpitOpening = false;
    return;
  }
  void capture(() => desktopBridge.openCockpit()).then((result) => {
    if (wizardOpen || setupRequested) {
      cockpitOpening = false;
      return;
    }
    if (!result.ok) renderIpcFailure('无法打开 Argus 工作台', result.detail);
    else mountCockpit(result.value);
  });
}

function runnerDescription(path: string): string {
  if (runnerKind !== 'pi') return path;
  const model = piConfiguration.qualifiedModel || 'Pi 当前默认模型（未在 settings.json 中固定）';
  return `${path}\n模型：${model}`;
}

function renderRunner(): void {
  if (!runnerSelected) {
    runnerStatus.dataset.state = 'warn';
    runnerStatus.textContent = '尚未选择';
    runnerPath.textContent = '请选择已安装并登录的 Agent CLI；检测到可执行文件不代表已完成登录。';
    chooseRunnerEl.disabled = true;
    chooseRunnerLabel.textContent = '请先选择 Agent CLI';
    clearRunnerEl.hidden = true;
    wizardNext.disabled = true;
    return;
  }
  chooseRunnerEl.disabled = false;
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
    runnerPath.textContent = `未找到 ${RUNNER_LABELS[runnerKind]}。请先安装并登录，或手动选择可执行文件。`;
    clearRunnerEl.hidden = true;
  }
  chooseRunnerLabel.textContent = `选择 ${RUNNER_LABELS[runnerKind]}`;
  wizardNext.disabled = !manual && !detected;
}

function renderRunnerKind(): void {
  for (const button of runnerKindButtons) {
    const selected = runnerSelected && button.dataset.kind === runnerKind;
    button.classList.toggle('is-selected', selected);
    button.setAttribute('aria-pressed', String(selected));
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
  summaryUrl.textContent = `127.0.0.1:${Number(portInput.value)}`;
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
  wizardNext.disabled = step === 0 && (
    !runnerSelected || !(runnerBins[runnerKind] || detectedRunners[runnerKind])
  );
  wizardCaption.textContent = STEP_LABELS[step] || '';
  if (step === 2) renderSummary();
  panels.find((panel) => !panel.hidden)?.querySelector<HTMLElement>('input, button, h3')?.focus();
}

function showWizard(setup: DesktopSetup): void {
  if (!wizardOpen) returnFocus = document.activeElement as HTMLElement | null;
  wizardOpen = true;
  wizardFinish.disabled = false;
  wizardFinish.textContent = cockpitMounted ? '保存设置' : '开始使用';
  wizardError.hidden = true;
  portError.hidden = true;
  wizardEl.hidden = false;
  cockpitEl.inert = true;
  splashEl.inert = true;
  desktopMenuBar.inert = true;
  splashEl.classList.add('has-wizard');
  document.documentElement.dataset.settingsMode = cockpitMounted ? 'true' : 'false';
  wizardCancel.hidden = !cockpitMounted;
  applySetup(setup);
  trialKey.value = '';
  trialForm.hidden = true;
  wizardEl.classList.remove('trial-entry-active');
  stepperEl.hidden = false;
  trialOpen.setAttribute('aria-expanded', 'false');
  trialOpen.className = 'primary';
  trialOpen.textContent = setup.trialMode ? '更换内部测试 Key' : '输入内部测试 Key';
  trialProgress.textContent = '';
  resetTrialDownload();
  portInput.value = String(port);
  renderRunnerKind();
  renderRunner();
  goToStep(0);
}

async function reopenWizard(): Promise<void> {
  if (applying || wizardOpen || wizardPending) return;
  setupRequested = true;
  wizardPending = true;
  const result = await capture(() => desktopBridge.getSetup());
  if (!result.ok) {
    renderIpcFailure('无法读取桌面设置', result.detail);
    return;
  }
  wizardPending = false;
  showWizard(result.value);
}

function closeWizard(): void {
  if (applying) return;
  wizardOpen = false;
  wizardEl.hidden = true;
  cockpitEl.inert = false;
  splashEl.inert = false;
  desktopMenuBar.inert = false;
  splashEl.classList.remove('has-wizard');
  document.documentElement.dataset.settingsMode = 'false';
  setupRequested = false;
  trialKey.value = '';
  returnFocus?.focus();
}

function closeDesktopMenus(): void {
  fileMenu.hidden = true;
  helpMenu.hidden = true;
  fileMenuTrigger.setAttribute('aria-expanded', 'false');
  helpMenuTrigger.setAttribute('aria-expanded', 'false');
}

function toggleDesktopMenu(
  trigger: HTMLButtonElement,
  menu: HTMLElement,
): void {
  const opening = menu.hidden;
  closeDesktopMenus();
  if (!opening) return;
  menu.hidden = false;
  trigger.setAttribute('aria-expanded', 'true');
  menu.querySelector<HTMLButtonElement>('button')?.focus();
}

async function runDesktopMenuAction(action: string): Promise<void> {
  closeDesktopMenus();
  if (action === 'settings') {
    await reopenWizard();
    return;
  }
  if (action === 'new-chat') {
    postToCockpit('argus:new-chat');
    return;
  }
  if (action === 'hide') {
    await desktopBridge.hideDesktop();
    return;
  }
  if (action === 'stop-quit') {
    if (window.confirm('停止本地后端会中断正在运行的任务。确定停止并退出吗？')) {
      await desktopBridge.stopBackendAndQuit();
    }
    return;
  }
  if (action === 'restart') {
    if (!window.confirm('重启本地后端会中断当前连接及正在进行的工作。确定重启吗？')) return;
    await desktopBridge.restartBackend();
    return;
  }
  if (action === 'open-logs') {
    await desktopBridge.openLogs();
    return;
  }
  if (action === 'open-data') {
    await desktopBridge.openData();
    return;
  }
  if (action === 'diagnostics') {
    const path = await desktopBridge.exportDiagnostics();
    if (path) window.alert(`脱敏诊断已导出：${path}`);
    return;
  }
  if (action === 'check-update') {
    renderUpdate(await desktopBridge.checkForUpdate());
    return;
  }
  if (action === 'about') await desktopBridge.showAbout();
}

function updateMessage(status: UpdateStatus): {
  kicker: string;
  title: string;
  detail: string;
  showInstall: boolean;
  showSecurity: boolean;
} {
  if (status.state === 'available') {
    const notes = (status.notes || '已发现可安装的新版本。').slice(0, 900);
    return {
      kicker: '发现新版本',
      title: `Argus ${status.availableVersion || '更新'} 已准备好`,
      detail: `${notes}\n\n请在合适的任务边界查看并安装；安装前会验证更新包签名。`,
      showInstall: true,
      showSecurity: true,
    };
  }
  if (status.state === 'downloading') {
    const progress = typeof status.progress === 'number' ? ` · ${status.progress}%` : '';
    return {
      kicker: '正在准备更新',
      title: `正在下载${progress}`,
      detail: '下载完成后将校验签名，并交给 Windows 安装器完成更新。',
      showInstall: false,
      showSecurity: true,
    };
  }
  if (status.state === 'installing') {
    return {
      kicker: '正在安装',
      title: '正在验证并安装更新',
      detail: '安装器接管后会安全重启 Argus。',
      showInstall: false,
      showSecurity: true,
    };
  }
  if (status.state === 'error') {
    return {
      kicker: '手动检查结果',
      title: '暂时无法检查更新',
      detail: status.detail || '请稍后重试；当前版本不会受到影响。',
      showInstall: false,
      showSecurity: false,
    };
  }
  if (status.state === 'up-to-date') {
    return {
      kicker: '手动检查结果',
      title: '已是最新版本',
      detail: `当前运行的是 Argus ${status.currentVersion}。`,
      showInstall: false,
      showSecurity: false,
    };
  }
  if (status.state === 'idle' && status.detail) {
    return {
      kicker: '预览构建',
      title: '发布更新已禁用',
      detail: status.detail,
      showInstall: false,
      showSecurity: false,
    };
  }
  return {
    kicker: '手动检查更新',
    title: '正在检查更新',
    detail: '仅从配置的 HTTPS 更新源读取更新信息。',
    showInstall: false,
    showSecurity: false,
  };
}

function renderUpdate(status: UpdateStatus): void {
  updateStatus = status;
  const message = updateMessage(status);
  const installing = ['downloading', 'installing'].includes(status.state);
  const manualFeedback = status.userInitiated
    && ['idle', 'checking', 'up-to-date', 'error'].includes(status.state);
  // A background check is deliberately invisible unless it found a real newer
  // package. Manual checks retain visible feedback because the user asked for it.
  const visible = status.state === 'available' || installing || manualFeedback;
  updateEl.hidden = !visible;
  if (!visible) return;
  updateEl.dataset.state = status.state;
  updateKickerEl.textContent = message.kicker;
  updateTitleEl.textContent = message.title;
  updateDetailEl.textContent = message.detail;
  updateSecurityEl.hidden = !message.showSecurity;
  updateInstallEl.hidden = !message.showInstall;
  updateInstallEl.disabled = status.state !== 'available';
  updateCheckEl.hidden = status.state === 'available' || installing;
  updateCheckEl.disabled = status.state === 'checking';
  updateDismissEl.hidden = installing;
  updateDismissEl.textContent = status.state === 'available' ? '稍后' : '关闭';
}

retryEl.addEventListener('click', () => {
  retryEl.disabled = true;
  statusEl.textContent = '正在重新启动 Argus';
  void capture(() => desktopBridge.restartBackend()).then((result) => {
    if (!result.ok) renderIpcFailure('无法重新启动 Argus', result.detail);
  }).finally(() => {
    retryEl.disabled = false;
  });
});

setupEl.addEventListener('click', () => void reopenWizard());

function revealTrial(): void {
  trialForm.hidden = false;
  wizardEl.classList.add('trial-entry-active');
  stepperEl.hidden = true;
  wizardCaption.textContent = '内部测试';
  trialOpen.className = 'ghost small';
  trialOpen.textContent = '使用自己的账号';
  trialOpen.setAttribute('aria-expanded', 'true');
  trialKey.focus();
}
trialOpen.addEventListener('click', () => {
  if (applying) return;
  if (trialForm.hidden) {
    revealTrial();
  } else {
    trialForm.hidden = true;
    trialKey.value = '';
    wizardEl.classList.remove('trial-entry-active');
    stepperEl.hidden = false;
    trialOpen.className = 'primary';
    trialOpen.textContent = '输入内部测试 Key';
    trialOpen.setAttribute('aria-expanded', 'false');
    goToStep(0);
  }
});
trialShortcut.addEventListener('click', async () => {
  await reopenWizard();
  if (wizardOpen) revealTrial();
});
desktopBridge.onTrialProgress((message) => {
  if (!trialBusy) return;
  resetTrialDownload();
  trialProgress.textContent = message;
});
function resetTrialDownload(): void {
  clearTimeout(trialDownloadTimer);
  trialDownloadTimer = undefined;
  trialDownload.hidden = true;
  trialNetworkHint.hidden = true;
  trialDownloadBar.removeAttribute('value');
  trialDownloadDetails.textContent = '';
}

function renderTrialDownload({ downloaded_bytes: downloaded, total_bytes: total }: TrialDownloadProgress): void {
  if (!trialBusy) return;
  clearTimeout(trialDownloadTimer);
  trialNetworkHint.hidden = true;
  trialDownload.hidden = false;
  trialProgress.textContent = '正在下载 Copilot…';
  const megabytes = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (total !== null && total > 0) {
    const percent = Math.min(100, Math.floor(downloaded / total * 100));
    trialDownloadBar.value = percent;
    trialDownloadDetails.textContent = `${percent}% · ${megabytes(downloaded)} / ${megabytes(total)}`;
  } else {
    trialDownloadBar.removeAttribute('value');
    trialDownloadDetails.textContent = downloaded > 0 ? `已下载 ${megabytes(downloaded)}` : '正在连接下载服务器…';
  }
  trialDownloadTimer = setTimeout(() => {
    trialNetworkHint.hidden = false;
  }, 30_000);
}
desktopBridge.onTrialDownload(renderTrialDownload);
trialForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (applying) return;
  const key = trialKey.value.trim();
  if (!/^argus_trial_[a-f0-9]{64}$/.test(key)) {
    trialProgress.textContent = '请输入完整的内部测试 Key。';
    trialKey.focus();
    return;
  }
  applying = trialBusy = true;
  resetTrialDownload();
  trialKey.value = '';
  trialKey.disabled = trialSubmit.disabled = true;
  wizardEl.setAttribute('aria-busy', 'true');
  trialSubmit.textContent = '正在准备…';
  trialProgress.textContent = '正在验证 Key…';
  const result = await capture(() => desktopBridge.completeTrialSetup(key));
  resetTrialDownload();
  applying = trialBusy = false;
  trialKey.disabled = trialSubmit.disabled = false;
  wizardEl.removeAttribute('aria-busy');
  trialSubmit.textContent = '开始试用';
  if (!result.ok || !result.value.ok) {
    // Never reflect raw IPC errors, which could contain serialized input.
    trialProgress.textContent = result.ok ? (result.value.error || '配置失败，请重试。') : '无法连接本地安装服务，请重试。';
    return;
  }
  closeWizard();
  const status = await capture(() => desktopBridge.getStatus());
  if (status.ok) render(status.value);
  else renderIpcFailure('无法读取本地服务状态', status.detail);
});

diagnosticsEl.addEventListener('click', () => {
  diagnosticsEl.disabled = true;
  void capture(() => desktopBridge.exportDiagnostics()).then((result) => {
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
  const selectedKind = runnerKind;
  chooseRunnerEl.disabled = true;
  const result = await capture(() => desktopBridge.chooseRunner(selectedKind));
  chooseRunnerEl.disabled = false;
  if (!result.ok) {
    runnerStatus.dataset.state = 'warn';
    runnerStatus.textContent = '选择失败';
    runnerPath.textContent = result.detail;
    return;
  }
  if (result.value) {
    runnerBins[selectedKind] = result.value;
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
    if (isRunnerKind(kind)) {
      runnerKind = kind;
      runnerSelected = true;
    }
    renderRunnerKind();
    renderRunner();
  });
}

portInput.addEventListener('input', () => {
  portError.hidden = isPortValid();
  portInput.setAttribute('aria-invalid', String(!isPortValid()));
});

refreshRunners.addEventListener('click', async () => {
  refreshRunners.disabled = true;
  const result = await capture(() => desktopBridge.getSetup());
  refreshRunners.disabled = false;
  if (result.ok) {
    detectedRunners = { ...result.value.detectedRunners };
    piConfiguration = result.value.piConfiguration;
    renderRunner();
  } else {
    wizardError.textContent = `检测失败：${result.detail}`;
    wizardError.hidden = false;
  }
});

wizardCancel.addEventListener('click', closeWizard);
wizardBack.addEventListener('click', () => {
  if (currentStep > 0) goToStep(currentStep - 1);
});
wizardNext.addEventListener('click', () => {
  if (currentStep === 0) {
    if (!runnerSelected || !(runnerBins[runnerKind] || detectedRunners[runnerKind])) return;
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
  if (applying) return;
  if (!runnerSelected || !(runnerBins[runnerKind] || detectedRunners[runnerKind])) {
    goToStep(0);
    return;
  }
  if (!isPortValid()) {
    goToStep(1);
    portError.hidden = false;
    portInput.focus();
    return;
  }
  port = Number(portInput.value);
  applying = true;
  wizardError.hidden = true;
  wizardBody.inert = true;
  wizardFinish.disabled = true;
  wizardBack.disabled = true;
  wizardCancel.disabled = true;
  wizardFinish.textContent = '正在保存并连接…';
  wizardEl.setAttribute('aria-busy', 'true');

  const invocation = await capture(() => desktopBridge.completeSetup({
    port,
    runnerKind,
    runnerBins,
  }));
  applying = false;
  wizardBody.inert = false;
  wizardFinish.disabled = false;
  wizardBack.disabled = false;
  wizardCancel.disabled = false;
  wizardEl.setAttribute('aria-busy', 'false');
  wizardFinish.textContent = cockpitMounted ? '保存设置' : '开始使用';
  if (!invocation.ok || !invocation.value.ok) {
    // Keep both the draft settings and the mounted cockpit. Validation/IPC
    // failures must stay visible without destroying an unsent conversation.
    wizardError.textContent = invocation.ok
      ? (invocation.value.error || '设置保存失败，请重试。')
      : `设置保存失败：${invocation.detail}`;
    wizardError.hidden = false;
    wizardError.focus();
    return;
  }
  closeWizard();
  const url = await capture(() => desktopBridge.openCockpit());
  if (!url.ok) {
    renderIpcFailure('设置已保存，但工作台尚未连接', url.detail);
    return;
  }
  mountCockpit(url.value);
  cockpitFrame.focus();
});

updateInstallEl.addEventListener('click', () => {
  updateInstallEl.disabled = true;
  void capture(() => desktopBridge.installUpdate()).then((result) => {
    if (!result.ok) renderUpdate({
      state: 'error',
      currentVersion: updateStatus?.currentVersion || 'unknown',
      userInitiated: true,
      detail: result.detail,
    });
  });
});

updateCheckEl.addEventListener('click', () => {
  void capture(() => desktopBridge.checkForUpdate()).then((result) => {
    if (result.ok) renderUpdate(result.value);
    else renderUpdate({
      state: 'error',
      currentVersion: updateStatus?.currentVersion || 'unknown',
      userInitiated: true,
      detail: result.detail,
    });
  });
});

updateDismissEl.addEventListener('click', () => {
  updateEl.hidden = true;
  void desktopBridge.dismissUpdate();
});

fileMenuTrigger.addEventListener('click', () => {
  toggleDesktopMenu(fileMenuTrigger, fileMenu);
});
helpMenuTrigger.addEventListener('click', () => {
  toggleDesktopMenu(helpMenuTrigger, helpMenu);
});
for (const button of desktopMenuActions) {
  button.addEventListener('click', () => {
    const action = button.dataset.menuAction;
    if (!action) return;
    void capture(() => runDesktopMenuAction(action)).then((result) => {
      if (!result.ok) window.alert(`桌面操作失败：${result.detail}`);
    });
  });
}
document.addEventListener('pointerdown', (event) => {
  if (!desktopMenuBar.contains(event.target as Node)) closeDesktopMenus();
});

window.addEventListener('keydown', (event) => {
  if (wizardOpen && event.key === 'Tab') {
    const focusable = Array.from(wizardEl.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]'))
      .filter((element) => element.getClientRects().length > 0 && !element.closest('[inert]'));
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && (document.activeElement === first || !wizardEl.contains(document.activeElement))) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !wizardEl.contains(document.activeElement))) {
      event.preventDefault();
      first?.focus();
    }
  }
  if (event.key === 'Escape') closeDesktopMenus();
  if (event.ctrlKey && event.key === ',') {
    event.preventDefault();
    closeDesktopMenus();
    void reopenWizard();
  }
  if (!wizardOpen && event.ctrlKey && event.key.toLowerCase() === 'n') {
    event.preventDefault();
    closeDesktopMenus();
    postToCockpit('argus:new-chat');
  }
  if (wizardOpen && !applying && event.key === 'Escape' && cockpitMounted) {
    event.preventDefault();
    closeWizard();
  }
});

cockpitFrame.addEventListener('load', hideSplashAfterCockpitLoad);

window.addEventListener('message', (event) => {
  if (event.source !== cockpitFrame.contentWindow || event.origin !== cockpitOrigin()) return;
  const data = event.data;
  if (!data || typeof data !== 'object') return;
  const type = (data as { type?: unknown }).type;
  const payload = (data as { payload?: unknown }).payload;
  if (type === 'argus:show-setup') void reopenWizard();
  if (type === 'argus:request-new-chat' && !wizardOpen) postToCockpit('argus:new-chat');
  if (type === 'argus:cockpit-interaction') closeDesktopMenus();
  if (type === 'argus:notify-delivery' || type === 'argus:notify-completion') {
    void desktopBridge.notifyDelivery(payload as Parameters<typeof desktopBridge.notifyDelivery>[0]);
  }
  if (type === 'argus:open-external' && typeof payload === 'string') {
    void desktopBridge.openExternal(payload);
  }
  if (type === 'argus:large-preview' && typeof payload === 'boolean') {
    void desktopBridge.setLargePreview(payload);
  }
  if (type === 'argus:theme-preference' && (payload === 'light' || payload === 'dark' || payload === 'system')) {
    if (appearanceTheme !== payload) {
      appearanceTheme = payload;
      void capture(() => desktopBridge.setAppearance({ theme: payload })).then((result) => {
        if (!result.ok) window.alert('主题已应用，但未能保存。请检查桌面数据目录权限。');
      });
    }
  }
  if (type === 'argus:theme-changed' && (payload === 'light' || payload === 'dark')) {
    // The authenticated cockpit owns the visible theme. Keep both trusted
    // renderer chrome rows and the native Windows caption on that same palette.
    cockpitTheme = payload;
    applyTheme(payload);
    void desktopBridge.setWindowTheme(payload);
  }
});

desktopBridge.onNewChat(() => postToCockpit('argus:new-chat'));
desktopBridge.onOpenDelivery((payload) => postToCockpit('argus:open-delivery', payload));
desktopBridge.onShowSetup(() => void reopenWizard());
desktopBridge.onUpdateStatus(renderUpdate);
desktopBridge.onStatus(render);

void loadAppearance().then(() => capture(() => desktopBridge.getStatus())).then((status) => {
  if (status.ok) render(status.value);
  else renderIpcFailure('无法读取本地服务状态', status.detail);
});
void capture(() => desktopBridge.getUpdateStatus()).then((status) => {
  if (status.ok) renderUpdate(status.value);
});
