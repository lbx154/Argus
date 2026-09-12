import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { groupPublicIncrements, ObservationViewer } from '../admin-data/ObservationViewer';
import type { ObservedEpisode, ObservedEvent } from '../admin-data/types';

function episode(events: ObservedEvent[]): ObservedEpisode {
  return {
    tenant_id: 'trial-11', sid: 's-visible', episode_id: 42, task_id: null,
    role: 'unknown', label: 'Unassigned', session_id: 'session', runtime: { run_label: 'simple-1' },
    capture_policy: 'recorded', state: 'complete', started_at: 100, updated_at: 120,
    collection: { event_count: events.length, event_counts: { tool_result: events.length }, complete: true,
      issues: [], historical_data_unavailable: false },
    quality: { state: 'not_approved', approved: false }, tool_pairs: [], tool_pairs_total: 0,
    tool_pairs_truncated: false, events,
  };
}

describe('administrator process viewer', () => {
  it('retains equal deltas and separates different content blocks and messages', () => {
    const delta = (sequence: number, message: number, content: number): ObservedEvent => ({
      sequence, kind: 'message_delta', payload: { type: 'text_delta', delta: 'same', message_index: message, content_index: content },
    });
    const groups = groupPublicIncrements([delta(0, 1, 0), delta(1, 1, 0), delta(2, 1, 1), delta(3, 2, 0)]);
    expect(groups.map(group => group.events.map(event => event.sequence))).toEqual([[0, 1], [2], [3]]);
    expect(groups[0].events.map(event => event.payload.delta).join('')).toBe('samesame');
  });

  it('bounds the rendered event list and keeps expanded bodies out of the initial DOM', () => {
    const events = Array.from({ length: 2_001 }, (_, sequence): ObservedEvent => ({
      sequence, kind: 'tool_result', observed_at: 100 + sequence,
      payload: { toolName: 'read', isError: false, content: `${'visible preview '.repeat(80)}BODY_ONLY_${sequence}` },
    }));
    const markup = renderToStaticMarkup(<ObservationViewer episode={episode(events)} readonly={false} onDownload={() => undefined} />);
    expect(markup.match(/class="admin-data-event"/g)).toHaveLength(60);
    expect(markup).toContain('Next event group');
    expect(markup).not.toContain('BODY_ONLY_');
    expect(markup).not.toContain('admin-data-event-body');
  });

  it('keeps collection completion distinct from quality and does not date an unassociated task', () => {
    const markup = renderToStaticMarkup(<ObservationViewer episode={episode([])} readonly onDownload={() => undefined} />);
    expect(markup).toContain('Call ended');
    expect(markup).toContain('Not reviewed');
    expect(markup).toContain('Project-level / no task association');
    expect(markup).not.toContain('Before task creation');
    expect(markup).toMatch(/<button[^>]*title="Exports are disabled for read-only sessions"[^>]*disabled=""/);
    expect(markup).toContain('Unassigned role');
  });
});
