import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ResourceStatus } from '../../../core/src/resourceStatus.generated';
import { ResourceStatusView } from '../components/ResourceStatus';

describe('ResourceStatusView', () => {
  it('renders degraded probe details', () => {
    const status: ResourceStatus = {
      schema_version: 1,
      enforcement: 'advisory',
      accelerators: [
        { kind: 'cuda', status: 'degraded', device_count: 1, detail: 'could not parse one telemetry row' },
        { kind: 'rocm', status: 'absent', device_count: 0, detail: 'rocm-smi is not installed' },
      ],
    };

    const markup = renderToStaticMarkup(<ResourceStatusView status={status} error="" />);

    expect(markup).toContain('Recommendations only');
    expect(markup).toContain('Limited · Devices: 1');
    expect(markup).toContain('could not parse one telemetry row');
    expect(markup).toContain('rocm-smi is not installed');
  });
  it('shows host indices, models and distinct telemetry states without guessing availability', () => {
    const device = { index: '0', name: 'NVIDIA A100', total_memory_mib: 40960, used_memory_mib: 0, utilization_percent: 0 };
    const status: ResourceStatus = {
      schema_version: 1, enforcement: 'strict',
      accelerators: [{ kind: 'cuda', status: 'available', device_count: 3, detail: '', devices: [
        { ...device, index: '3', utilization_percent: 72, used_memory_mib: 10240 },
        { ...device, index: '7', used_memory_mib: 512 },
        { ...device, index: '9' },
      ] }],
    };
    const html = renderToStaticMarkup(<ResourceStatusView status={status} error="" />);
    for (const state of ['computing', 'allocated', 'quiet']) expect(html).toContain(`data-gpu-state="${state}"`);
    for (const index of ['3', '7', '9']) expect(html).toContain(`GPU ${index}`);
    expect(html).toContain('NVIDIA A100');
    expect(html).toContain('72%');
    expect(html).toContain('10.0 GiB / 40.0 GiB');
    expect(html).toContain('512 MiB');
  });
  it('does not invent device cards when telemetry is inaccessible or absent', () => {
    const html = renderToStaticMarkup(<ResourceStatusView status={{
      schema_version: 1, enforcement: 'advisory',
      accelerators: [{ kind: 'cuda', status: 'inaccessible', device_count: 0, detail: 'permission denied', devices: [] }],
    }} error="" />);
    expect(html).toContain('permission denied');
    expect(html).not.toContain('data-gpu-state=');
  });
});
