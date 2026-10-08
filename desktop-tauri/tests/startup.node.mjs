import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

function fixture() {
  let now = 0, nextFrame = 0;
  const frames = new Map(), marks = new Map();
  const document = { hidden: false, hasFocus: () => true, elementFromPoint: () => pupil };
  const pupil = { getBoundingClientRect: () => ({ x: 0, y: 0, width: 10, height: 10 }) };
  const animation = { currentTime: 200 };
  const eye = { querySelector: () => pupil, contains: element => element === pupil,
    getAnimations: () => [animation] };
  const exports = {};
  const source = readFileSync(new URL('../src/startup.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  runInNewContext(compiled, { exports, document,
    performance: { now: () => now, clearMarks: name => marks.delete(name),
      mark: (name, options) => marks.set(name, options?.startTime ?? now) },
    requestAnimationFrame: callback => { frames.set(++nextFrame, callback); return nextFrame; },
    cancelAnimationFrame: id => frames.delete(id),
  });
  const cycle = exports.visibleEyeCycle({ eye, nativeVisible: async () => true,
    enabled: () => true, motionEnabled: () => true });
  let finished = false;
  cycle.finished.then(() => { finished = true; });
  return { marks, document, animation, cycle, get finished() { return finished; },
    async sample(frameTime, observedTime = frameTime) {
      now = observedTime;
      const [id, callback] = frames.entries().next().value ?? [];
      assert.equal(typeof callback, 'function', 'A visible cycle still needs another frame.');
      frames.delete(id);
      callback(frameTime);
      // Flush native visibility and completion promises without real-time sleeps.
      for (let turn = 0; turn < 4; turn++) await Promise.resolve();
    },
  };
}

test('a late first frame cannot spend time before the eye was actually exposed', async () => {
  const clock = fixture();
  await clock.sample(0, 200); // Native visibility is still being queried.
  await clock.sample(0, 200); // Exposure is established 200 ms after the frame timestamp.
  assert.equal(clock.marks.get('argus:splash-visible'), 200);
  assert.equal(clock.animation.currentTime, 0);
  await clock.sample(216);
  for (let now = 316; now <= 1216; now += 100) {
    await clock.sample(now);
    assert.equal(clock.finished, false, 'Unexposed first-frame time must not count.');
  }
  await clock.sample(1240);
  assert.equal(clock.finished, false, '1040 ms of exposure cannot complete the 1050 ms cycle.');
  await clock.sample(1250);
  assert.equal(clock.finished, true);
});

test('a stalled renderer cannot spend a whole visible cycle in one frame', async () => {
  const clock = fixture();
  await clock.sample(0);
  await clock.sample(0);
  await clock.sample(2000);
  assert.equal(clock.finished, false);
  for (let now = 2100; now <= 2900; now += 100) await clock.sample(now);
  assert.equal(clock.finished, false);
  await clock.sample(3000);
  assert.equal(clock.finished, true);
});

test('hidden intervals do not advance the visible cycle', async () => {
  const clock = fixture();
  await clock.sample(0);
  await clock.sample(0);
  clock.document.hidden = true;
  await clock.sample(100);
  await clock.sample(2000);
  clock.document.hidden = false;
  await clock.sample(2100);
  for (let now = 2200; now <= 3100; now += 100) await clock.sample(now);
  assert.equal(clock.finished, false);
  await clock.sample(3150);
  assert.equal(clock.finished, true);
});
