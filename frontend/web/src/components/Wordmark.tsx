/** Plain product mark: one neutral registration block and a word, with no
 * gradient lettering or mythic ornament. */
export function Wordmark({ size = 20, tag }: { size?: number; tag?: string }) {
  return (
    <span className="inline-flex items-center gap-2.5 select-none">
      <span className="inline-flex items-center gap-2" style={{ fontSize: size, fontWeight: 650, letterSpacing: '-0.025em' }}>
        <span aria-hidden="true" className="inline-block h-[0.62em] w-[0.62em] border border-ink-faint bg-ink-dim" />
        <span className="text-ink">argus</span>
      </span>
      {tag && <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-ink-faint">{tag}</span>}
    </span>
  );
}
