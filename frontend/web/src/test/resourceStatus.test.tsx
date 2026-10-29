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

    expect(markup).toContain('Usage shown; Argus does not limit resources');
    expect(markup).toContain('Limited · 1 GPU');
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

it('reports 128 CPU cores and an idle compute sample with 1 MiB still allocated', () => {
  const html = renderToStaticMarkup(<ResourceStatusView status={{ schema_version: 1, enforcement: 'advisory', accelerators: [
    { kind: 'cpu', status: 'available', device_count: 128, detail: '' },
    { kind: 'cuda', status: 'available', device_count: 1, detail: '', devices: [
      { index: '0', name: 'GPU', total_memory_mib: 40960, used_memory_mib: 1, utilization_percent: 0 },
    ] },
  ] }} error="" />);
  expect(html).toContain('128 cores');
  expect(html).not.toContain('Devices: 128');
  expect(html).toContain('0% compute load · 1 MiB used');
  expect(html).toContain('1 MiB / 40.0 GiB');
});

 it('names the GPU count as a quantity', () => {
  const html = renderToStaticMarkup(<ResourceStatusView status={{ schema_version: 1, enforcement: 'advisory', accelerators: [
    { kind: 'cuda', status: 'available', device_count: 4, detail: '' },
  ] }} error="" />);
  expect(html).toContain('4 GPUs');
  expect(html).not.toContain('Devices:');
});
