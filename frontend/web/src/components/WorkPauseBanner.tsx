import type { Snapshot } from '../api';

/** A runtime can pause work and supply the reason shown to the operator. */
export function WorkPauseBanner({ pause }: { pause: Snapshot['work_pause'] }) {
  if (!pause) return null;
  return <div data-work-pause role="alert" className="mx-3 mt-3 rounded-lg border border-err/50 bg-err/10 px-3.5 py-2 text-[13px] leading-5 text-err">
    {pause.reason}
  </div>;
}
