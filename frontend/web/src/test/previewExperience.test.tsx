import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { ArtifactInfo, DeliveryReceipt } from '../../../core/src/types';
import { DeliveryNotice } from '../components/DeliveryNotice';
import { MissionControl } from '../components/MissionControl';
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

  it('acknowledges an opened delivery so its notice no longer covers file controls', () => {
    const delivery: DeliveryReceipt = { schema_version: 1, delivery_id: 'fixture', item_id: 'task', kind: 'task_completed', title: 'Result', summary: '', status: 'done', review_status: 'done', delivered_at: 1, primary_target: null, targets: [] };
    const onOpen = vi.fn();
    const onDismiss = vi.fn();
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(createElement(DeliveryNotice, { delivery, onOpen, onDismiss })); });
    act(() => renderer.root.findAllByType('button')[0].props.onClick());
    expect(onOpen).toHaveBeenCalledWith(delivery);
    expect(onDismiss).toHaveBeenCalledWith('fixture');
    act(() => renderer.unmount());
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

describe('role identity and focused workbench', () => {
  it.each(['idle', 'active', 'done', 'error'])('keeps four distinct role markers when roles are %s', (status) => {
    const view = emptyMissionView();
    for (const role of view.roles) role.status = status;
    const html = renderToStaticMarkup(createElement(MissionControl, { view }));
    for (const role of ['manager', 'planner', 'engineer', 'reviewer']) {
      expect(html).toContain(`data-role-dot="${role}"`);
      expect(html).toContain(`background:rgb(var(--role-${role}))`);
    }
  });

  it('uses exactly the same three modules for every project and two overview destinations', () => {
    expect(WORKBENCH_MODULES.map((module) => module.id)).toEqual(['overview', 'experiments', 'ide']);
    expect(WORKSPACE_DESTINATIONS.map((module) => module.id)).toEqual(['experiments', 'ide']);
  });
});
