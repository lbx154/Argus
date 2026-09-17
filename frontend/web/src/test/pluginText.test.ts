import { describe, expect, it } from 'vitest';
import { pluginText } from '../lib/pluginText';

describe('plugin center language', () => {
  it('uses the host locale without changing protocol values or paths', () => {
    expect(pluginText('安装', 'en')).toBe('Install');
    expect(pluginText('安装', 'zh-CN')).toBe('安装');
    expect(pluginText('codex', 'en')).toBe('codex');
    expect(pluginText('0.4.0', 'en')).toBe('0.4.0');
  });
  it('translates ordinary repair consent, current errors and stale health without changing component ids', () => {
    for (const value of [
      '继续安装或修复前，请确认 PLATON 官方许可。确认后会在同一流程准备完整运行环境，无需另点其他修复按钮。',
      '同意并继续', '科学环境 · 上次检查结果',
      '下方为本次操作前的检查结果；请以上方最新错误为准，或重新检查环境。',
      '请先确认 PLATON 使用许可，再继续安装或修复依赖。',
      'PLATON 缺少 Windows Salford 运行库（0xC0000135）。请点击“修复依赖”，确认许可后会在同一流程准备完整运行环境。',
      '依赖修复完成', '依赖修复未完成',
    ]) expect(pluginText(value, 'en')).not.toMatch(/[\u3400-\u9fff]/);
    const error = pluginText('依赖修复未完成：dials, platon。请查看组件检查结果；不会报告修复成功。', 'en');
    expect(error).toContain('dials, platon');
    expect(error).not.toMatch(/[\u3400-\u9fff]/);
  });
  it('keeps component counts in translated health summaries', () => {
    const text = pluginText('科学环境 · 2 项待配置', 'en');
    expect(text).toContain('2');
    expect(text).not.toMatch(/[\u3400-\u9fff]/);
  });
});
