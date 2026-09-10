import { describe, expect, it } from 'vitest';
import { pluginText } from '../lib/pluginText';

describe('plugin center language', () => {
  it('uses the host locale without changing protocol values or paths', () => {
    expect(pluginText('安装', 'en')).toBe('Install');
    expect(pluginText('安装', 'zh-CN')).toBe('安装');
    expect(pluginText('codex', 'en')).toBe('codex');
    expect(pluginText('0.4.0', 'en')).toBe('0.4.0');
  });
  it('keeps component counts in translated health summaries', () => {
    const text = pluginText('科学环境 · 2 项待配置', 'en');
    expect(text).toContain('2');
    expect(text).not.toMatch(/[\u3400-\u9fff]/);
  });
});
