import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { DaemonSpendBadge, daemonSpendText } from '../components/DaemonSpendBadge';

describe('DaemonSpendBadge', () => {
  it('formats small settled spend without rounding it to zero', () => {
    expect(daemonSpendText({ settledUsd: 0.0475, status: 'priced' })).toBe('$0.048');
  });

  it('marks partial ledger totals and exposes call detail', () => {
    const markup = renderToStaticMarkup(
      React.createElement(DaemonSpendBadge, {
        settledUsd: null,
        knownUsd: 0.08,
        status: 'partial',
        calls: 3,
        premiumRequests: 2,
        live: true,
      }),
    );

    expect(markup).toContain('$0.080+');
    expect(markup).toContain('3 model calls');
    expect(markup).toContain('2.0 premium requests');
    expect(markup).toContain('animate-pulse');
  });
});
