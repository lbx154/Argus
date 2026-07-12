export function formatStructuredData(raw: string): string {
  const text = raw.trim();
  if (!text) return '';
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    try {
      const rows = text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
      return rows.map((row) => JSON.stringify(row, null, 2)).join('\n');
    } catch {
      return raw;
    }
  }
}

export function parseDelimited(raw: string, delimiter: ',' | '\t'): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let index = 0; index < raw.length; index++) {
    const char = raw[index];
    if (char === '"') {
      if (quoted && raw[index + 1] === '"') {
        cell += '"';
        index++;
      } else {
        quoted = !quoted;
      }
    } else if (char === delimiter && !quoted) {
      row.push(cell);
      cell = '';
    } else if ((char === '\n' || char === '\r') && !quoted) {
      if (char === '\r' && raw[index + 1] === '\n') index++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
    } else {
      cell += char;
    }
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((cells) => cells.some((value) => value.length > 0));
}

export function JsonPreview({ value }: { value: string }) {
  return (
    <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-5 font-mono text-xs leading-6 text-ink-dim scroll-thin">
      {formatStructuredData(value) || '(empty data)'}
    </pre>
  );
}

export function TablePreview({
  value,
  delimiter,
}: {
  value: string;
  delimiter: ',' | '\t';
}) {
  const rows = parseDelimited(value, delimiter).slice(0, 200);
  const header = rows[0] ?? [];
  return (
    <div className="min-h-0 flex-1 overflow-auto p-4 scroll-thin">
      {rows.length ? (
        <table className="w-full border-collapse text-left text-xs">
          <thead>
            <tr>
              {header.slice(0, 40).map((cell, index) => (
                <th key={index} className="border border-line/60 bg-surface px-2 py-1.5 font-semibold text-ink">{cell}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(1).map((cells, rowIndex) => (
              <tr key={rowIndex}>
                {header.slice(0, 40).map((_, columnIndex) => (
                  <td key={columnIndex} className="border border-line/50 px-2 py-1.5 align-top text-ink-dim">
                    {cells[columnIndex] ?? ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : <div className="text-sm text-ink-faint">(empty table)</div>}
    </div>
  );
}
