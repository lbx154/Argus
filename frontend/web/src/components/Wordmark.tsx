import { useId } from 'react';

function useGradientId(prefix: string): string {
  return `${prefix}-${useId().replaceAll(':', '')}`;
}

export function ArgusMark({ size, className = 'text-ink' }: { size: number; className?: string }) {
  const id = useGradientId('argus-rounded-mark');
  return (
    <svg data-logo="rounded-mark" viewBox="0 0 512 512" role="img" aria-label="Argus" style={{ width: size, height: size }} className={`shrink-0 ${className}`}>
      <defs>
        <linearGradient id={id} gradientUnits="userSpaceOnUse" x1="66" y1="0" x2="440" y2="0">
          <stop offset="0%" stopColor="rgb(var(--spectral-blue))" />
          <stop offset="52%" stopColor="rgb(var(--spectral-violet))" />
          <stop offset="100%" stopColor="rgb(var(--spectral-gold))" />
        </linearGradient>
      </defs>
      <path d="M352 112q0-30 30-30h28q30 0 30 30v320h-88v-52q-46 62-129 62Q66 442 66 266T228 88q80 0 124 56v-32ZM140 266q46-80 102-80t110 80q-54 80-110 80t-102-80Z" fill={`url(#${id})`} fillRule="evenodd" />
      <path d="M286 266A42 42 0 1 0 202 266A42 42 0 1 0 286 266ZM274 248A12 12 0 1 0 250 248A12 12 0 1 0 274 248Z" fill={`url(#${id})`} fillRule="evenodd" />
    </svg>
  );
}

function RoundedLockup({ size }: { size: number }) {
  const id = useGradientId('argus-rounded-horizontal');
  return (
    <svg data-logo="rounded-horizontal" viewBox="150 40 1160 390" role="img" aria-label="Argus" style={{ width: size * 2.75, height: size }} className="shrink-0">
      <defs>
        <linearGradient id={id} gradientUnits="userSpaceOnUse" x1="180" y1="0" x2="1280" y2="0">
          <stop offset="0%" stopColor="rgb(var(--spectral-blue))" />
          <stop offset="52%" stopColor="rgb(var(--spectral-violet))" />
          <stop offset="100%" stopColor="rgb(var(--spectral-gold))" />
        </linearGradient>
      </defs>
      <g transform="translate(180 92) scale(.54)">
        <path d="M352 112q0-30 30-30h28q30 0 30 30v320h-88v-52q-46 62-129 62Q66 442 66 266T228 88q80 0 124 56v-32ZM140 266q46-80 102-80t110 80q-54 80-110 80t-102-80Z" fill={`url(#${id})`} fillRule="evenodd" />
        <path d="M286 266A42 42 0 1 0 202 266A42 42 0 1 0 286 266ZM274 248A12 12 0 1 0 250 248A12 12 0 1 0 274 248Z" fill={`url(#${id})`} fillRule="evenodd" />
      </g>
      <g fill={`url(#${id})`}>
        <path d="M383 556Q394 556 409 555Q424 554 433 552L422 412Q415 414 401.5 415.5Q388 417 378 417Q340 417 305 403.5Q270 390 248.5 360Q227 330 227 278V0H78V546H191L213 454H220Q244 496 286 526Q328 556 383 556Z" transform="translate(444 334) scale(.36 -.36)" />
        <path d="M255 556Q356 556 413 476H417L429 546H555V-1Q555-118 486-179Q417-240 282-240Q224-240 174.5-233Q125-226 78-208V-89Q179-131 291-131Q406-131 406-7V4Q406 21 407.5 39Q409 57 410 71H406Q378 28 339 9Q300-10 251-10Q154-10 99.5 64.5Q45 139 45 272Q45 406 101 481Q157 556 255 556ZM302 435Q197 435 197 270Q197 107 304 107Q361 107 388.5 139.5Q416 172 416 253V271Q416 359 389 397Q362 435 302 435Z" transform="translate(617.52 334) scale(.36 -.36)" />
        <path d="M579 546V0H465L445 70H437Q411 28 365.5 9Q320-10 269-10Q181-10 128 37.5Q75 85 75 190V546H224V227Q224 169 245 139Q266 109 312 109Q380 109 405 155.5Q430 202 430 289V546Z" transform="translate(855.48 334) scale(.36 -.36)" />
        <path d="M459 162Q459 79 400.5 34.5Q342-10 226-10Q169-10 128-2.5Q87 5 46 22V145Q90 125 141 112Q192 99 231 99Q275 99 293.5 112Q312 125 312 146Q312 160 304.5 171Q297 182 272 196Q247 210 194 232Q143 254 110 275.5Q77 297 61 327.5Q45 358 45 404Q45 480 104 518Q163 556 261 556Q312 556 358 546Q404 536 453 513L408 406Q368 423 332 434.5Q296 446 259 446Q193 446 193 410Q193 397 201.5 386.5Q210 376 234.5 364Q259 352 307 332Q354 313 388 292.5Q422 272 440.5 241.5Q459 211 459 162Z" transform="translate(1102.08 334) scale(.36 -.36)" />
      </g>
    </svg>
  );
}

export function Wordmark({ size = 20, tag, compact = false }: { size?: number; tag?: string; compact?: boolean }) {
  return (
    <span className="inline-flex select-none items-center gap-2.5">
      {compact ? <ArgusMark size={size} /> : <RoundedLockup size={size} />}
      {tag && !compact ? <span className="text-xs font-medium uppercase tracking-[0.08em] text-ink-faint">{tag}</span> : null}
    </span>
  );
}
