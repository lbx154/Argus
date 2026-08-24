import type { DashboardData, Idea } from '../types'

const ideas: Idea[] = [
  {
    id: 'idea-cache-causal',
    title: 'Causal cache surgery for long-context agents',
    thesis: 'Replace proxy eviction scores with intervention-tested token value, then prove when a compressed trace preserves tool-use decisions.',
    field: 'Efficient ML · Agents',
    novelty: 88,
    feasibility: 74,
    freshness: 93,
    delta: 'Moves from correlation-based cache importance to causal intervention under agent trajectories.',
    sources: [
      { kind: 'arXiv', label: '3 nearby papers', age: '6h' },
      { kind: 'OpenReview', label: '11 accepted comparables', age: '2d' },
      { kind: 'GitHub', label: '2 baseline changes', age: '42m' },
    ],
    compute: '≈ 310 GPU·h on configured pool',
    risk: 'Baseline fidelity · evaluation leakage',
  },
  {
    id: 'idea-runtime-proof',
    title: 'Proof-carrying recovery for agent runtimes',
    thesis: 'Attach machine-checkable evidence to every recovery decision so a resumed agent cannot silently diverge from its research contract.',
    field: 'Systems · Reliability',
    novelty: 82,
    feasibility: 86,
    freshness: 79,
    delta: 'Treats recovery correctness as an evidence protocol, not process liveness.',
    sources: [
      { kind: 'arXiv', label: '1 possible collision', age: '1d' },
      { kind: 'OpenReview', label: '7 accepted comparables', age: '3d' },
      { kind: 'GitHub', label: '14 repos tracked', age: '19m' },
    ],
    compute: '≈ 140 GPU·h + fault injection',
    risk: 'Artifact scope · systems variance',
  },
  {
    id: 'idea-private-review',
    title: 'Private reviewer ensembles with calibrated dissent',
    thesis: 'Preserve reviewer disagreement instead of averaging it away, using dissent-aware calibration to expose brittle claims before submission.',
    field: 'Trustworthy ML · Evaluation',
    novelty: 76,
    feasibility: 91,
    freshness: 84,
    delta: 'Optimizes detection of hidden claim failure rather than mean reviewer agreement.',
    sources: [
      { kind: 'arXiv', label: '5 nearby papers', age: '4h' },
      { kind: 'OpenReview', label: '19 accepted comparables', age: '1d' },
      { kind: 'GitHub', label: 'No exact implementation', age: '3h' },
    ],
    compute: '≈ 85 GPU·h + API budget',
    risk: 'Judge contamination · circular scoring',
  },
]

export const demoData: DashboardData = {
  conferences: [
    { id: 'neurips-2027', acronym: 'NeurIPS', name: 'Neural Information Processing Systems', deadline: '2027-05-15', deadlineEnd: '2027-05-22', kind: 'forecast', track: 'Main', area: 'ML', reminderDays: 265, color: '#7C6CF2', ideas },
    { id: 'iclr-2027', acronym: 'ICLR', name: 'International Conference on Learning Representations', deadline: '2026-09-25', deadlineEnd: '2026-10-02', kind: 'forecast', track: 'Main', area: 'ML', reminderDays: 33, color: '#2BB9A7', ideas: [ideas[0], ideas[2]] },
    { id: 'sigmod-2027', acronym: 'SIGMOD', name: 'ACM SIGMOD Conference', deadline: '2026-10-17', kind: 'official', track: 'Round 2', area: 'Data', reminderDays: 55, color: '#E2A84B', ideas: [ideas[1], ideas[0]] },
    { id: 'cvpr-2027', acronym: 'CVPR', name: 'Computer Vision and Pattern Recognition', deadline: '2026-11-13', deadlineEnd: '2026-11-20', kind: 'forecast', track: 'Main', area: 'Vision', reminderDays: 82, color: '#EF6A72', ideas: [ideas[2], ideas[0]] },
    { id: 'usenix-sec-2027', acronym: 'USENIX Sec.', name: 'USENIX Security Symposium', deadline: '2027-02-05', kind: 'official', track: 'Cycle 1', area: 'Security', reminderDays: 166, color: '#5EA1FF', ideas: [ideas[1], ideas[2]] },
  ],
  campaigns: [
    {
      id: 'cmp-causal-cache', title: 'Causal cache surgery', venue: 'ICLR 2027', status: 'running', executionState: 'running', connectionId: 'conn-local', launchTriggered: true, launchEligible: false, canStart: false, canRetryStart: false, canPause: true, canDrain: true, canReview: true, canLockContract: false, phase: 'Pilot falsification', progress: 47,
      summary: 'The planner is testing whether intervention-derived token value predicts downstream tool selection better than attention and recency proxies.',
      objective: 'Establish whether causal token interventions can support a compute-efficient cache policy without degrading multi-step agent task completion.',
      branch: 'campaign/causal-cache', source: 'Local workstation', commit: 'c22de7c', releasePinned: true, releaseReference: 'c22de7c', releaseReferenceSource: 'campaign-config', elapsed: '18h 42m', gpuHours: 73.4, budgetGpuHours: 160, tasksDone: 17, tasksTotal: 36,
      roles: [
        { name: 'Manager', state: 'active', task: 'Monitoring falsifier threshold' },
        { name: 'Planner', state: 'waiting', task: 'Awaiting seed 4 evidence' },
        { name: 'Engineer', state: 'active', task: 'Running LongBench agent slice' },
        { name: 'Reviewer', state: 'idle', task: 'Next review at evidence freeze' },
      ],
      events: [
        { time: '14:32:08', actor: 'Engineer', text: 'Completed seed 3/5 · cache ratio 0.35 · 41 artifacts indexed' },
        { time: '14:28:41', actor: 'Manager', text: 'Evidence path advanced; process health verified independently from PID' },
        { time: '14:07:12', actor: 'Engineer', text: 'Baseline H2O provenance check passed against upstream commit' },
        { time: '13:58:04', actor: 'Reviewer', text: 'Flagged possible evaluator leakage in task-family split', level: 'warn' },
        { time: '13:44:29', actor: 'Planner', text: 'Revised falsifier: retain held-out tool families until confirmatory phase' },
      ],
      artifacts: [
        { name: 'pilot_seed_03.parquet', type: 'results', size: '18.4 MB', state: 'verified' },
        { name: 'baseline_manifest.json', type: 'provenance', size: '22 KB', state: 'verified' },
        { name: 'leakage_audit.md', type: 'review', size: '8 KB', state: 'needs response' },
        { name: 'intervention_map.svg', type: 'figure', size: '1.2 MB', state: 'draft' },
      ],
      claims: [
        { id: 'cl-1', claim: 'Intervention score predicts tool-use sensitivity.', strength: 'partial', evidence: '4/5 seeds · 2 datasets', updated: '6m ago' },
        { id: 'cl-2', claim: 'Policy reduces KV footprint without task loss.', strength: 'blocked', evidence: 'Confirmatory set remains frozen', updated: '18m ago' },
        { id: 'cl-3', claim: 'Overhead remains below 5% at inference.', strength: 'supported', evidence: 'Profiler runs n=24', updated: '1h ago' },
      ],
      prompt: `ROLE\nYou are the orchestrator of a bounded, falsifiable research campaign.\n\nVENUE CONTRACT\nTarget: ICLR 2027 · Main track · forecast deadline interval.\n\nOBJECTIVE\nTest whether causal token intervention can replace proxy cache scores while preserving downstream tool decisions.\n\nINTEGRITY\nDo not require a positive result. Keep the confirmatory split frozen. Named baselines must run the authors' method and record upstream commit provenance.\n\nSTOP CONDITIONS\nReturn NO_WINNER if the effect vanishes across three seeds or overhead exceeds 15%.`,
    },
    {
      id: 'cmp-proof-runtime', title: 'Proof-carrying recovery', venue: 'SIGMOD 2027', status: 'review', executionState: 'completed', connectionId: 'conn-gpu-01', launchTriggered: true, launchEligible: false, canStart: false, canRetryStart: false, canPause: false, canDrain: false, canReview: true, canLockContract: true, phase: 'Independent review', progress: 78,
      summary: 'The implementation and fault-injection matrix are complete. An independent viewer is challenging recovery equivalence claims.',
      objective: 'Make research-agent recovery decisions replayable and externally verifiable.', branch: 'campaign/proof-recovery', source: 'gpu-node-01', commit: '455da6c', releasePinned: true, releaseReference: '455da6c', releaseReferenceSource: 'campaign-config', elapsed: '6d 4h', gpuHours: 118, budgetGpuHours: 190, tasksDone: 39, tasksTotal: 50,
      roles: [{ name: 'Manager', state: 'waiting', task: 'Awaiting viewer certificate' }, { name: 'Planner', state: 'idle', task: 'Plan frozen' }, { name: 'Engineer', state: 'idle', task: 'Experiments complete' }, { name: 'Reviewer', state: 'active', task: 'Adversarial trace audit' }],
      events: [{ time: '12:14:20', actor: 'Reviewer', text: 'Opened challenge: recovery equivalence under partial artifact loss', level: 'warn' }, { time: '11:49:08', actor: 'Manager', text: 'Frozen evidence bundle e94a11' }],
      artifacts: [{ name: 'recovery_matrix.csv', type: 'results', size: '4.1 MB', state: 'verified' }, { name: 'evidence_bundle.tar.zst', type: 'bundle', size: '312 MB', state: 'frozen' }],
      claims: [{ id: 'cl-4', claim: 'Recovery is trace-equivalent after mission-boundary drain.', strength: 'partial', evidence: '36/40 fault scenarios', updated: '2h ago' }],
      prompt: 'Bounded recovery protocol evaluation. Preserve every failure trace and stop on unverifiable equivalence.',
    },
    {
      id: 'cmp-dissent-review', title: 'Calibrated reviewer dissent', venue: 'NeurIPS 2027', status: 'attention', executionState: 'needs_attention', connectionId: 'conn-gpu-02', launchTriggered: true, launchEligible: false, canStart: false, canRetryStart: false, canPause: false, canDrain: false, canReview: false, canLockContract: true, phase: 'Novelty collision', progress: 22,
      summary: 'A new arXiv preprint overlaps with the calibration mechanism. The campaign is paused for a claim-difference decision.',
      objective: 'Use structured reviewer dissent to reveal brittle paper claims.', branch: 'campaign/reviewer-dissent', source: 'gpu-node-02', commit: 'c22de7c', releasePinned: true, releaseReference: 'c22de7c', releaseReferenceSource: 'campaign-config', elapsed: '3d 7h', gpuHours: 21, budgetGpuHours: 120, tasksDone: 8, tasksTotal: 34,
      roles: [{ name: 'Manager', state: 'waiting', task: 'Human novelty decision' }, { name: 'Planner', state: 'idle', task: 'Collision map complete' }, { name: 'Engineer', state: 'idle', task: 'No experiment approved' }, { name: 'Reviewer', state: 'active', task: 'Comparing claim boundaries' }],
      events: [{ time: '09:18:51', actor: 'Reviewer', text: 'Material novelty collision found in arXiv:2608.10412', level: 'warn' }],
      artifacts: [{ name: 'collision_matrix.md', type: 'novelty', size: '17 KB', state: 'needs decision' }],
      claims: [{ id: 'cl-5', claim: 'Dissent calibration detects hidden claim brittleness.', strength: 'blocked', evidence: 'Novelty contract not locked', updated: '5h ago' }],
      prompt: 'Research the closest mechanisms before any experiment. A positive outcome is not required.',
    },
  ],
  viewerReports: [
    { id: 'vr-1', campaignId: 'cmp-causal-cache', venue: 'ICLR 2027', updated: '8m ago', verdict: 'Promising, evidence still below submission bar', overall: 6.2, confidence: 0.71, oralReadiness: 34, dimensions: [
      { label: 'Originality', score: 7.4, note: 'Mechanism is distinct if causal score survives frozen evaluation.' }, { label: 'Soundness', score: 5.8, note: 'Evaluator leakage challenge remains open.' }, { label: 'Significance', score: 6.7, note: 'Potentially broad across agent runtimes.' }, { label: 'Clarity', score: 6.1, note: 'Core causal estimand needs a compact definition.' }, { label: 'Reproducibility', score: 5.2, note: 'Confirmatory bundle not yet available.' },
    ], blockers: ['Close task-family leakage challenge', 'Run the locked confirmatory split', 'Add a cost-quality Pareto analysis'] },
    { id: 'vr-2', campaignId: 'cmp-proof-runtime', venue: 'SIGMOD 2027', updated: '2h ago', verdict: 'Borderline accept pending fault-model closure', overall: 7.1, confidence: 0.78, oralReadiness: 58, dimensions: [
      { label: 'Originality', score: 7.0, note: 'Evidence protocol framing is differentiated.' }, { label: 'Soundness', score: 7.2, note: 'Four scenarios remain unresolved.' }, { label: 'Significance', score: 7.6, note: 'Directly addresses agent runtime reliability.' }, { label: 'Clarity', score: 6.8, note: 'Definitions are internally consistent.' }, { label: 'Reproducibility', score: 7.1, note: 'Frozen artifact bundle is available.' },
    ], blockers: ['Resolve partial-artifact-loss equivalence', 'Add cross-filesystem fault scenario'] },
  ],
  connections: [
    { id: 'conn-local', name: 'Local workstation', kind: 'local', address: 'http://127.0.0.1:8799', state: 'connected', version: 'c22de7c', latency: '4 ms', capabilities: ['Configured GPU pool', 'Pi', 'GitHub Copilot'], backendReady: true },
    { id: 'conn-gpu-01', name: 'gpu-node-01', kind: 'remote', address: 'https://argus-gpu-01.demo.invalid', state: 'connected', version: '455da6c', latency: '26 ms', capabilities: ['8× A100 80 GB', 'SLURM', 'Codex'], backendReady: true },
    { id: 'conn-gpu-02', name: 'gpu-node-02', kind: 'remote', address: 'https://argus-gpu-02.demo.invalid', state: 'offline', version: 'unknown', latency: '—', capabilities: ['2× H100 80 GB', 'Docker'] },
  ],
  approvals: [
    { id: 'ap-1', title: 'Resolve novelty collision', campaign: 'Calibrated reviewer dissent', kind: 'Claim boundary', requested: '5h ago', risk: 'high', detail: 'A new preprint overlaps with the calibration objective. Choose shrink, pivot, or stop before experiments begin.' },
    { id: 'ap-2', title: 'Unlock confirmatory split', campaign: 'Causal cache surgery', kind: 'Evidence gate', requested: '18m ago', risk: 'medium', detail: 'Pilot passed its predeclared threshold. Unlocking starts the five-seed frozen evaluation and consumes ≈96 GPU·h.' },
    { id: 'ap-3', title: 'Approve reviewer revision', campaign: 'Proof-carrying recovery', kind: 'Review response', requested: '2h ago', risk: 'low', detail: 'The revision adds two fault scenarios without changing the central claim or evaluation metric.' },
  ],
  resources: {
    gpus: [0, 1, 2, 3].map((i) => ({ id: `gpu-${i}`, label: `Example GPU · ${i}`, memory: '48 GB', host: 'Local workstation', enabled: true })),
    pools: [{ id: 'demo-local-pool', label: 'Local workstation', type: 'gpu_pool', enabled: true }, { id: 'demo-api-pool', label: 'API research budget', type: 'api_only', enabled: true }],
    roles: [
      { role: 'Manager', provider: 'Pi', model: 'github-copilot/claude-sonnet-4.5', budget: '$24 / day' },
      { role: 'Planner', provider: 'Codex', model: 'gpt-5.4', budget: '$32 / campaign' },
      { role: 'Engineer', provider: 'GitHub Copilot', model: 'claude-sonnet-4.5', budget: '$40 / day' },
      { role: 'Reviewer', provider: 'OpenAI API', model: 'gpt-5.4', budget: '$18 / review' },
    ],
  },
}
