import assert from 'node:assert/strict';
import test from 'node:test';
import { executeProcess, type ProcessExit } from '../src/process.js';

const cases = [
  { name: 'empty output', text: '', limit: 8, lines: [] },
  { name: 'empty lines and no phantom final line', text: '\n\r\n\n', limit: 8, lines: ['', '', ''] },
  { name: 'exact limit and per-line reset', text: '12345678\nabcdefgh\nABCDEFGH', limit: 8,
    lines: ['12345678', 'abcdefgh', 'ABCDEFGH'] },
  { name: 'CRLF at the byte limit', text: '1234567\r\nlast\r', limit: 8, lines: ['1234567', 'last'] },
  { name: 'CR counts toward the byte limit', text: '12345678\r\n', limit: 8, lines: [], overflow: true },
  { name: 'terminated overflow', text: '123456789\n', limit: 8, lines: [], overflow: true },
  { name: 'unterminated overflow', text: '123456789', limit: 8, lines: [], overflow: true },
  { name: 'split UTF-8 at exact limit', text: 'A\u20ac\ud83c\udf0d\nA\u20ac\ud83c\udf0d', limit: 8,
    lines: ['A\u20ac\ud83c\udf0d', 'A\u20ac\ud83c\udf0d'] },
  { name: 'UTF-8 bytes rather than character count', text: 'A\u20ac\ud83c\udf0d', limit: 7,
    lines: [], overflow: true },
  { name: 'decoder EOF replacement at limit', bytes: [0x61, 0xe2, 0x82], limit: 4, lines: ['a\ufffd'] },
  { name: 'decoder EOF replacement exceeds limit', bytes: [0x61, 0xe2, 0x82], limit: 3,
    lines: [], overflow: true },
];

for (const stream of ['stdout', 'stderr'] as const) {
  for (const scenario of cases) {
    test(`${stream}: ${scenario.name}`, { timeout: 10_000 }, async () => {
      const bytes = scenario.bytes ?? [...Buffer.from(scenario.text!)];
      const source = `
        for (const byte of ${JSON.stringify(bytes)}) {
          process.${stream}.write(Buffer.from([byte]));
          await new Promise(resolve => setTimeout(resolve, 2));
        }
      `;
      const lines: string[] = [];
      let result: ProcessExit | undefined;
      for await (const event of executeProcess({
        executable: process.execPath, args: ['--input-type=module', '-e', source],
        cwd: process.cwd(), input: '', maxLineBytes: scenario.limit, maxBufferedBytes: 64,
        wallTimeoutMs: 5_000, idleTimeoutMs: 3_000, terminateGraceMs: 100,
      })) {
        if (event.type === 'line') {
          assert.equal(event.stream, stream);
          lines.push(event.line);
        } else result = event;
      }
      assert.deepEqual(lines, scenario.lines);
      assert.ok(result);
      assert.equal(result.stopKind, scenario.overflow ? 'output_limit' : null);
      if (!scenario.overflow) assert.equal(result.code, 0);
    });
  }
}
