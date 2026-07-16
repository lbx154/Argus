#!/usr/bin/env node

import process from 'node:process';

import { run } from './launcher.mjs';

// Keep setup under the product command while preserving the compatibility
// `argus-skill` administrative entrypoint.
run(process.argv.slice(2).includes('--setup') ? 'cli' : 'tui');
