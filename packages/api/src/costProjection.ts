import { READ_API, isJsonObject, validProjectId, type ProjectCostRow, type ProjectCosts } from '@argus/contracts';
import { UsageSummaryAccumulator } from '@argus/runtime';

/** Internal bridge frames are folded before the HTTP response is committed. */
export class CostProjectionStream {
  private active: { id: string; summary: UsageSummaryAccumulator } | null = null;
  private readonly seen = new Set<string>();
  private readonly projects: ProjectCostRow[] = [];
  private records = 0;

  constructor(private readonly limit: number) {}

  consume(value: unknown): void {
    if (!isJsonObject(value) || Object.hasOwn(value, 'ok')) throw new Error('invalid cost input frame');
    if (value.kind === 'cost_project') {
      if (this.active || !validProjectId(value.project_id) || this.seen.has(value.project_id)
        || this.projects.length >= this.limit || this.projects.length >= READ_API.max_projects) {
        throw new Error('invalid project boundary');
      }
      this.seen.add(value.project_id);
      this.active = { id: value.project_id, summary: new UsageSummaryAccumulator({
        maxRecords: READ_API.max_cost_records, maxIdentities: READ_API.max_cost_identities,
      }) };
    } else if (value.kind === 'cost_record') {
      if (!this.active || this.records >= READ_API.max_cost_records) throw new Error('unexpected or excessive usage records');
      this.active.summary.add(value.record);
      this.records += 1;
    } else if (value.kind === 'cost_project_end') {
      if (!this.active || value.project_id !== this.active.id
        || typeof value.updated_at !== 'number' || !Number.isFinite(value.updated_at)) throw new Error('invalid project completion');
      const summary = this.active.summary.snapshot();
      this.projects.push({
        id: this.active.id, spend_usd: summary.cost_usd, known_cost_usd: summary.known_cost_usd,
        spend_status: summary.pricing_status, usage_calls: summary.call_count,
        premium_requests: summary.premium_requests, updated_at: value.updated_at,
      });
      this.active = null;
    } else throw new Error('unknown cost input frame');
  }

  finish(value: unknown): ProjectCosts {
    if (this.active || !isJsonObject(value) || value.project_count !== this.projects.length
      || typeof value.generated_at !== 'number' || !Number.isFinite(value.generated_at)) {
      throw new Error('incomplete cost input stream');
    }
    return { projects: this.projects, generated_at: value.generated_at };
  }
}
