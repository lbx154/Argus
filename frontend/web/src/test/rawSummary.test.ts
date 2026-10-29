import { describe, expect, it } from 'vitest';
import { lastMeaningfulLine, outputSummary, plainLogLine } from '../lib/rawSummary';

describe('lastMeaningfulLine', () => {
  it('returns the last line that says something, without its timestamp and level', () => {
    const tail = [
      '2026-09-11 22:07:01,123 INFO argus.daemon: round 3 started',
      '2026-09-11 22:07:04,000 ERROR argus.daemon: Copilot CLI exited with code 1.',
      '',
      '----',
    ].join('\n');
    expect(lastMeaningfulLine(tail)).toBe('Copilot CLI exited with code 1.');
  });

  it('is empty for an empty tail and clips a long line', () => {
    expect(lastMeaningfulLine('')).toBe('');
    expect(lastMeaningfulLine(`x${'y'.repeat(400)}`, 40)).toHaveLength(40);
  });

  it('strips bracketed timestamps and levels but keeps the message', () => {
    expect(plainLogLine('[2026-09-11T22:07:01Z] [WARNING] budget nearly spent')).toBe('budget nearly spent');
    expect(plainLogLine('plain message with 2026-09-11 inside')).toBe('plain message with 2026-09-11 inside');
  });
});

describe('outputSummary', () => {
  it('gives the first line and the line count', () => {
    expect(outputSummary('research-idea-playbook\nwriting-checklist\n\nsource-index')).toEqual({ first: 'research-idea-playbook', lines: 3 });
    expect(outputSummary('')).toEqual({ first: '', lines: 0 });
  });
});
