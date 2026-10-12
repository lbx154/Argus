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

    expect(markup).toContain('Recommendations only');
    expect(markup).toContain('Limited · Devices: 1');
    expect(markup).toContain('could not parse one telemetry row');
    expect(markup).toContain('Devices: 1');
    expect(markup).toContain('train the reranker');
    expect(markup).toContain('1m 30s left');
    expect(markup).toContain('Resource release requested · checkpoint for an urgent evaluation');
    expect(markup).toContain('Resources kept · unsafe checkpoint boundary');
    expect(markup).toContain('Queue position 1');
    expect(markup).toContain('45s left');
    expect(markup).not.toContain('train-1');
    expect(markup).not.toContain('eval-2');
  });
  it('shows host indices, models and distinct telemetry / reservation states without guessing availability', () => {
    const device = { index: '0', name: 'NVIDIA A100', total_memory_mib: 40960, used_memory_mib: 0, utilization_percent: 0, reserved: false };
    const status: ResourceStatus = {
      schema_version: 1, enforcement: 'strict', holders: [], queue: [],
      accelerators: [{ kind: 'cuda', status: 'available', device_count: 4, detail: '', devices: [
        { ...device, index: '3', utilization_percent: 72, used_memory_mib: 10240 },
        { ...device, index: '5', reserved: true },
        { ...device, index: '7', used_memory_mib: 512 },
        { ...device, index: '9' },
      ] }],
    };
    const html = renderToStaticMarkup(<ResourceStatusView status={status} error="" />);
    for (const state of ['computing', 'reserved', 'allocated', 'quiet']) expect(html).toContain(`data-gpu-state="${state}"`);
    for (const index of ['3', '5', '7', '9']) expect(html).toContain(`GPU ${index}`);
    expect(html).toContain('NVIDIA A100');
    expect(html).toContain('72%');
    expect(html).toContain('10.0 GiB / 40.0 GiB');
    expect(html).toContain('512 MiB');
    expect(html).toContain('not a guarantee of exclusive availability');
  });
  it('does not invent device cards when telemetry is inaccessible or absent', () => {
    const html = renderToStaticMarkup(<ResourceStatusView status={{
      schema_version: 1, enforcement: 'advisory', holders: [], queue: [],
      accelerators: [{ kind: 'cuda', status: 'inaccessible', device_count: 0, detail: 'permission denied', devices: [] }],
    }} error="" />);
    expect(html).toContain('permission denied');
    expect(html).not.toContain('data-gpu-state=');
  });
});
