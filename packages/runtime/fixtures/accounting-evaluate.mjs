// Test-only protocol used by Python's differential checks against the built JS.
import { extractTokenUsage, quoteTokenUsage } from '../dist/index.js';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const { streams, quotes } = JSON.parse(input);
process.stdout.write(JSON.stringify({
  usages: streams.map(extractTokenUsage),
  quotes: quotes.map(({ model, counts }) => quoteTokenUsage(model, counts)),
}));
