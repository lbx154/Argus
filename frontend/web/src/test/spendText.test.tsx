import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { AccountQuota, MissionUsage } from '../../../core/src/types';
import { AccountQuotaChip, AccountQuotaPanel, MissionSpendLine } from '../components/SpendSummary';
import { translate } from '../i18n';
import { accountQuotaChip, accountQuotaText, missionSpendParts } from '../lib/spendText';

const en = (key: string, vars?: Record<string, string | number>) => translate(key, vars, 'en');
const zh = (key: string, vars?: Record<string, string | number>) => translate(key, vars, 'zh-CN');

function quota(partial: Partial<AccountQuota>): AccountQuota {
  return {
    provider: 'copilot', login: 'someone', plan: 'p', billing_mode: 'request',
    entitlement: 300, remaining: 37, used: 263, percent_remaining: 12.3, reset_date: '2026-11-01',
    overage_permitted: false, unlimited: false, fetched_at: 0, low: false, warn_percent: 10,
    ...partial,
  };
}

const usage: MissionUsage = {
  calls: 15, premium_requests: 15, credits: 0, known_cost_usd: 0.6, pricing_status: 'priced',
  input_tokens: 1000, cached_input_tokens: 0, output_tokens: 100,
};

describe('spend text', () => {
  it('says how a request-billed account is billed and what is left, in both languages', () => {
    expect(accountQuotaText(quota({}), en)).toBe('Request-billed · 37 of 300 requests left this month · resets 2026-11-01');
    expect(accountQuotaText(quota({}), zh)).toContain('按次计费');
    expect(accountQuotaText(quota({}), zh)).toContain('37/300');
    expect(accountQuotaChip(quota({}))).toBe('37/300');
  });

  it('says how much of a credit-billed month is left', () => {
    const credit = quota({ billing_mode: 'credit', entitlement: 1_000_000, remaining: 576_234, percent_remaining: 57.6 });
    expect(accountQuotaText(credit, en)).toContain('Credit-billed · 58% of monthly credits left');
    expect(accountQuotaText(credit, zh)).toContain('按额度计费 · 本月额度还剩 58%');
    expect(accountQuotaChip(credit)).toBe('58%');
  });

  it('shows every unit a task spent, against the budget when one is set', () => {
    expect(missionSpendParts(usage, en)).toEqual(['15 requests', '$0.60', '15 calls']);
    expect(missionSpendParts(usage, en, { requests: 40, usd: 0 })).toEqual(['15 requests of 40', '$0.60', '15 calls']);
    expect(missionSpendParts(usage, zh)[0]).toBe('15 次请求');
    expect(missionSpendParts({ ...usage, calls: 0 }, en)).toEqual([]);
  });

  it('renders the low-quota warning and the task spend line', () => {
    const low = quota({ remaining: 5, percent_remaining: 1.7, low: true });
    const panel = renderToStaticMarkup(<AccountQuotaPanel quota={low} />);
    expect(panel).toContain('data-account-quota-low');
    expect(panel).toContain('Argus keeps working');
    expect(renderToStaticMarkup(<AccountQuotaChip quota={low} />)).toContain('data-low="true"');
    expect(renderToStaticMarkup(<AccountQuotaChip quota={null} />)).toBe('');
    expect(renderToStaticMarkup(<MissionSpendLine usage={usage} />)).toContain('This task · 15 requests');
  });
});
