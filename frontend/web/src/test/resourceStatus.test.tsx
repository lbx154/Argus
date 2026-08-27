import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ResourceStatus } from '../../../core/src/resourceStatus.generated';
import { ResourceStatusView } from '../components/ResourceStatus';

describe('ResourceStatusView', () => {
  it('renders degraded probe details and contended holders', () => {
    const status: ResourceStatus = {
      schema_version: 1,
      enforcement: 'advisory',
      accelerators: [
        { kind: 'cuda', status: 'degraded', device_count: 1, detail: 'could not parse one telemetry row' },
        { kind: 'rocm', status: 'absent', device_count: 0, detail: 'rocm-smi is not installed' },
      ],
      holders: [{
        project: 'training',
        task_id: 'train-1',
        intent: 'train the reranker',
        ttl_seconds: 90,
        device_count: 1,
        yield_requests: [{
          reason: 'checkpoint for an urgent evaluation',
          response: { decision: 'decline', reason: 'unsafe checkpoint boundary' },
        }],
      }],
      queue: [{
        position: 1,
        project: 'evaluation',
        task_id: 'eval-2',
        intent: 'run the held-out evaluation',
        ttl_seconds: 45,
      }],
    };

    const markup = renderToStaticMarkup(<ResourceStatusView status={status} error="" />);

    expect(markup).toContain('advisory mode');
    expect(markup).toContain('degraded · 1 devices');
    expect(markup).toContain('could not parse one telemetry row');
    expect(markup).toContain('training · train-1 · 1 devices');
    expect(markup).toContain('train the reranker');
    expect(markup).toContain('1m 30s left');
    expect(markup).toContain('Yield request · checkpoint for an urgent evaluation');
    expect(markup).toContain('decline · unsafe checkpoint boundary');
    expect(markup).toContain('#1 · evaluation · eval-2');
    expect(markup).toContain('45s left');
  });
});
