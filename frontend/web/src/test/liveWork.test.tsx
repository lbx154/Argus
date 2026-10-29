import { useRef } from 'react';
import { act, create } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { plainCommand } from '../lib/feedSteps';
import { useAutosizeTextarea } from '../useAutosizeTextarea';

describe('commands in plain words', () => {
  it('unwraps shell boilerplate from commands', () => {
    expect(plainCommand("/bin/bash -lc 'pytest -q tests'")).toBe('pytest -q tests');
    expect(plainCommand('bash -c "nvidia-smi"')).toBe('nvidia-smi');
    expect(plainCommand('python eval.py\nsecond line')).toBe('python eval.py');
  });

  it("cuts a path into a backend session's scratch files to its last name, and keeps the work's own paths", () => {
    const files = '/srv/state/copilot-home/session-state/09822296-0a3e/files';
    expect(plainCommand(`mkdir -p ${files} && pytest -q | tee ${files}/pytest.log`)).toBe('mkdir -p …/files && pytest -q | tee …/pytest.log');
    expect(plainCommand('git -C /work/repo status')).toBe('git -C /work/repo status');
  });

  it("cuts a path into Argus' own package or a shared wiki, and keeps a project's docs/wiki", () => {
    expect(plainCommand("rg -n '^name:' /opt/argus-src/argus/builtin_skills /srv/home/wiki/_shared_verticals/software -g SKILL.md"))
      .toBe("rg -n '^name:' …/builtin_skills …/software -g SKILL.md");
    expect(plainCommand('ls /repo/docs/wiki/pages')).toBe('ls /repo/docs/wiki/pages');
  });
});

describe('the message box follows its text both ways', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('shrinks back after text is deleted, even from a scrolled maximum', () => {
    vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
    const LINE = 28;
    const element = {
      value: '', style: {} as Record<string, string>, clientWidth: 400,
      get clientHeight() { return parseFloat(this.style.height || '0'); },
      // Like a browser: never less than the box itself.
      get scrollHeight() { return Math.max(this.value.split('\n').length * LINE, this.clientHeight); },
      getClientRects: () => [{}], addEventListener: vi.fn(), removeEventListener: vi.fn(),
    };
    function Box({ value }: { value: string }) {
      const ref = useRef<HTMLTextAreaElement>(null);
      element.value = value;
      useAutosizeTextarea(ref, value, 28, 132);
      return <textarea ref={ref} value={value} readOnly />;
    }
    let tree!: ReturnType<typeof create>;
    act(() => { tree = create(<Box value="one" />, { createNodeMock: () => element }); });
    expect(element.style.height).toBe('28px');
    act(() => tree.update(<Box value={'1\n2\n3\n4\n5\n6\n7'} />));
    expect(element.style.height).toBe('132px');
    expect(element.style.overflowY).toBe('auto');
    act(() => tree.update(<Box value={'1\n2'} />));
    expect(element.style.height).toBe('56px');
    expect(element.style.overflowY).toBe('hidden');
    act(() => tree.update(<Box value="" />));
    expect(element.style.height).toBe('28px');
  });
});
