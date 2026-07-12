export function SplitHandle({
  onPointerDown,
  onReset,
  onNudge,
  value,
  min = 240,
  max = 600,
  label = 'Resize panel',
}: {
  onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
  onReset: () => void;
  onNudge: (delta: number) => void;
  value: number;
  min?: number;
  max?: number;
  label?: string;
}) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onDoubleClick={onReset}
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft') {
          event.preventDefault();
          onNudge(-16);
        } else if (event.key === 'ArrowRight') {
          event.preventDefault();
          onNudge(16);
        } else if (event.key === 'Home') {
          event.preventDefault();
          onReset();
        }
      }}
      className="group relative hidden w-2 shrink-0 cursor-col-resize items-center justify-center outline-none lg:flex"
    >
      <span className="h-full w-px bg-line/30 transition-colors duration-150 group-hover:bg-blue/70 group-focus:bg-blue/70" />
    </div>
  );
}
