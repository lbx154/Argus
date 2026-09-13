/** One complete eye cycle measured only while the native window and pupil are visible. */
export function visibleEyeCycle(options: {
  eye: SVGElement;
  nativeVisible: () => Promise<boolean>;
  enabled: () => boolean;
  motionEnabled: () => boolean;
}): { finished: Promise<void>; cancel: () => void } {
  let settle!: () => void;
  const finished = new Promise<void>(resolve => { settle = resolve; });
  let stopped = false;
  let nativeVisible = false;
  let querying = false;
  let nextQuery = 0;
  let previous: number | undefined;
  let elapsed = 0;
  let painted = false;
  let frame = 0;
  const cancel = () => {
    if (stopped) return;
    stopped = true;
    cancelAnimationFrame(frame);
    settle();
  };
  const sample = (time: number) => {
    if (stopped) return;
    if (!querying && time >= nextQuery) {
      querying = true;
      void options.nativeVisible().then(value => { nativeVisible = value; })
        .catch(() => { nativeVisible = false; })
        .finally(() => { querying = false; nextQuery = performance.now() + 80; });
    }
    const pupil = options.eye.querySelector('.argus-mark-pupil');
    const box = pupil?.getBoundingClientRect();
    const exposed = box && box.width > 0 && options.eye.contains(
      document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2),
    );
    const visible = nativeVisible && !document.hidden && document.hasFocus()
      && options.enabled() && exposed;
    if (visible) {
      if (!painted) {
        painted = true;
        // Do not spend a cycle in a native window which WebView2 has created
        // but Windows has not shown yet. Start the original orbit at reveal.
        for (const animation of options.eye.getAnimations()) animation.currentTime = 0;
        performance.clearMarks('argus:splash-visible');
        performance.clearMarks('argus:cockpit-visible');
        performance.mark('argus:splash-visible');
      }
      // A blocked/background renderer must not count a large wall-clock gap
      // as visibly rendered animation. A full cycle still needs real frames.
      if (previous !== undefined) elapsed += Math.min(100, Math.max(0, time - previous));
      previous = time;
      if (!options.motionEnabled() || elapsed >= 1050) { cancel(); return; }
    } else {
      previous = undefined;
    }
    frame = requestAnimationFrame(sample);
  };
  frame = requestAnimationFrame(sample);
  return { finished, cancel };
}
