export interface DecisionEvidence {
  label: string;
  path: string;
  summary: string;
}

export interface DecisionOption {
  id: string;
  label: string;
  description: string;
  requires_note: boolean;
}

export interface OperatorDecisionCard {
  kind?: 'domain_intake';
  id: string;
  item_id: string;
  revision: number;
  status: 'pending' | 'resolved';
  title: string;
  reason: string;
  question: string;
  evidence: DecisionEvidence[];
  options: DecisionOption[];
  options_source?: 'agent' | 'workflow' | 'none';
  selected_option: string;
  note: string;
  legacy?: boolean;
  /** When this question was actually raised, if recorded by the producer. */
  asked_at?: number;
  task_title?: string;
  task_status?: string;
  is_current_task?: boolean;
}
