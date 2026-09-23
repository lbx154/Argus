// Test-only differential protocol; uses the compiled implementation.
import { summarizeUsage } from '../dist/index.js';
let input = '';
for await (const chunk of process.stdin) input += chunk;
process.stdout.write(JSON.stringify(JSON.parse(input).map(records => summarizeUsage(records))));
