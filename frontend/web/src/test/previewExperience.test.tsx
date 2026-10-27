import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { ArtifactInfo, DeliveryReceipt } from '../../../core/src/types';
import { PdfPreview, pdfContainScale } from '../components/PdfPreview';
import { LIVE_PROGRESS_PATH, ResearchCanvas } from '../components/ResearchCanvas';
import { preferredPreviewWidth } from '../lib/previewLayout';
import { WORKBENCH_MODULES, WORKSPACE_DESTINATIONS } from '../research-workbench/modules';

vi.mock('../hooks', () => ({ useArtifact: () => ({ data: undefined, isLoading: false, isError: false }) }));

describe('file reading experience', () => {
  it('gives an opened file about 45 percent while retaining a usable conversation', () => {
    expect(preferredPreviewWidth(1280, 256, true)).toBe(576);
    expect(preferredPreviewWidth(1440, 256, true)).toBe(648);
    expect(preferredPreviewWidth(1920, 256, true)).toBe(840);
    expect(preferredPreviewWidth(1024, 400, true)).toBe(320);
    expect(preferredPreviewWidth(1024, 256, false)).toBe(461);
  });

  it('fits oversized pages and shallow viewports rather than forcing a crop at 25 percent', () => {
    const scale = pdfContainScale(2400, 3600, 400, 240);
    expect(scale).toBeLessThan(0.25);
    expect(2400 * scale).toBeLessThanOrEqual(368);
    expect(3600 * scale).toBeLessThanOrEqual(208);
  });

  it('keeps zoomed canvas content in positive, scrollable space and offers fit-page recovery', () => {
    const html = renderToStaticMarkup(createElement(PdfPreview, { src: 'blob:fixture', name: 'sample.pdf' }));
    expect(html).toContain('pdf-scroll-viewport');
    expect(html).toContain('pdf-page-stage');
    expect(html).toContain('w-max');
    expect(html).toContain('max-w-none shrink-0');
    expect(html).toMatch(/适合页面|Fit page/);
  });

  it('expands only on explicit file selection, not when polling or showing live progress', () => {
    const onOpenFile = vi.fn();
    const file: ArtifactInfo = { path: 'preview.pdf', name: 'preview.pdf', exists: true, source: 'delivery', kind: 'pdf', mime: 'application/pdf', size: 100, mtime: 1, why: 'fixture' };
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(createElement(ResearchCanvas, {
      sid: 'fixture', missionView: emptyMissionView(), artifacts: [file], onExpand: vi.fn(), onOpenFile,
    })); });
    expect(onOpenFile).not.toHaveBeenCalled();
    act(() => renderer.root.findByType('select').props.onChange({ target: { value: 'preview.pdf' } }));
    expect(onOpenFile).toHaveBeenCalledTimes(1);
    act(() => renderer.root.findByType('select').props.onChange({ target: { value: LIVE_PROGRESS_PATH } }));
    expect(onOpenFile).toHaveBeenCalledTimes(1);
    act(() => renderer.unmount());
  });
});

describe('following the newest delivery', () => {
  it('keeps the chosen file while older deliveries load, and follows one made after opening by the server clock alone', () => {
    const stored = new Map([['argus.preview.file.v1:s', 'chosen.md']]);
    vi.stubGlobal('localStorage', { getItem: (key: string) => stored.get(key) ?? null, setItem: (key: string, value: string) => stored.set(key, value) });
    const artifact = (path: string): ArtifactInfo => ({ path, name: path, exists: true, source: 'delivery', kind: 'markdown', mime: 'text/markdown', size: 1, mtime: 1, why: 'fixture' });
    const receipt = (id: string, path: string, deliveredAt: number) => ({
      schema_version: 1, delivery_id: id, kind: 'task_completed', item_id: id, title: id, summary: '', status: 'done', review_status: 'done',
      delivered_at: deliveredAt, primary_target: { path, label: path, source: 'solo_output', why: '' }, targets: [{ path, label: path, source: 'solo_output', why: '' }],
    }) as unknown as DeliveryReceipt;
    const props = { sid: 's', filesFirst: true, missionView: emptyMissionView(), artifacts: ['chosen.md', 'old-a.md', 'old-b.md', 'new.md'].map(artifact), onExpand: vi.fn() };
    let renderer!: ReturnType<typeof create>;
    // Server times sit far behind this browser's clock: only server stamps are compared.
    act(() => { renderer = create(createElement(ResearchCanvas, { ...props, deliveriesLoaded: false, latestDelivery: receipt('d1', 'old-a.md', 1_000) })); });
    act(() => renderer.update(createElement(ResearchCanvas, { ...props, deliveriesLoaded: false, latestDelivery: receipt('d2', 'old-b.md', 2_000) })));
    act(() => renderer.update(createElement(ResearchCanvas, { ...props, deliveriesLoaded: true, latestDelivery: receipt('d2', 'old-b.md', 2_000) })));
    expect(stored.get('argus.preview.file.v1:s')).toBe('chosen.md');
    act(() => renderer.update(createElement(ResearchCanvas, { ...props, deliveriesLoaded: true, latestDelivery: receipt('d3', 'new.md', 2_005) })));
    expect(stored.get('argus.preview.file.v1:s')).toBe('new.md');
    act(() => renderer.unmount());
    vi.unstubAllGlobals();
  });
});

describe('role identity and focused workbench', () => {
  it('keeps the shared research tools available in project navigation and the overview', () => {
    expect(WORKBENCH_MODULES.map((module) => module.id)).toEqual(['timeline', 'experiments', 'ide', 'runtime']);
    expect(WORKSPACE_DESTINATIONS.map((module) => module.id)).toEqual(['timeline', 'experiments', 'ide']);
  });
});
