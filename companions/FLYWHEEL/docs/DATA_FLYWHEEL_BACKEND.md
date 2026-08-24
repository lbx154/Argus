# ARGUS / FLYWHEEL data contract

This companion stores research history; it does not train a model, submit a paper,
or start Argus. All irreversible-looking actions require an explicit human API call.

## Lifecycle

1. `POST /api/team-intakes/extract` creates a transparent deterministic draft.
2. `POST /api/team-intakes/{id}/confirm` creates the human-confirmed `team_profile`.
3. `POST /api/episodes` creates the mutable Episode head and optional entity links.
4. Review imports are staged, then independently confirmed with redaction and rights.
5. `POST /api/episodes/{id}/seal` appends an immutable, hash-chained revision.
6. Dataset selection is previewed, then sealed with the preview SHA-256.

Unconfirmed review payloads are not written to the immutable content-addressed store.
The content object is created only after a human confirms redaction and license basis.

## API shapes

### Episodes

- `GET /api/episodes` -> `{ "items": [EpisodeDetail] }`
- `POST /api/episodes` accepts:

```json
{
  "title": "required",
  "objective": "human-defined objective",
  "team_profile_id": "optional-confirmed-profile-id",
  "venue_id": 1,
  "deadline_id": 2,
  "ideation_run_id": "optional",
  "candidate_id": "required-with-ideation-run-id",
  "campaign_id": "actual-conditioned-candidate-execution-campaign",
  "training_consent": false,
  "license_basis": "",
  "metadata": {},
  "links": [{"entity_type": "artifact", "entity_id": "...", "relation": "supporting_material", "metadata": {}}]
}
```

When `ideation_run_id` is present, `candidate_id` and the actual candidate execution
`campaign_id` are mandatory and must match one immutable conditioned binding. The API
creates `execution`, `selected_candidate` and `ideation_source` links itself; client
requests cannot claim these reserved lineage relations or inject lineage metadata.
Generic/manual Episodes remain valid archives but report unverified lineage and are
never data-eligible.

- `GET /api/episodes/{id}` returns the Episode fields plus `revisions`, `links`,
  `review_imports`, `gates`, and `data_eligibility`.
- `POST /api/episodes/{id}/seal` accepts
  `{ "actor": "...", "reason": "...", "terminal_state": "submitted" }`.
  Objective and confirmed team profile are mandatory. Any draft review blocks sealing.
- `GET|POST /api/episodes/{id}/verify` returns
  `{ "valid", "checks", "head_revision", "manifest_sha256" }`.

`gates` contains `objective_present`, `team_confirmed`, `reviews_confirmed`,
`revision_sealed`, and `integrity_verified`. `data_eligibility` additionally reports
episode consent/license, review rights, and head integrity.

### Team intake

- `POST /api/team-intakes/extract` accepts `{ "raw_text": "..." }` and returns
  `{ "id", "state":"draft", "raw_text", "extracted", "uncertainties" }`.
- `POST /api/team-intakes/{id}/confirm` accepts
  `{ "actor", "name?", "profile", "training_consent", "license_basis" }` and
  returns the newly created `team_profile_id`. Confirmation is one-shot.

The extractor is deliberately local and deterministic. Its output is never treated as
truth until the human submits the confirm request.

### Review import

- `POST /api/episodes/{id}/review-imports` stages paste, JSON, or PDF evidence. Paste
  uses `{ "source_kind":"paste", "raw_text":"..." }`; JSON uses
  `{ "source_kind":"json", "payload": ... }`. PDF uses
  `{ "source_kind":"pdf", "payload": { "filename":"reviews.pdf",
  "mime_type":"application/pdf", "content_base64":"..." } }`. PDF input is strict
  base64, must begin with the `%PDF-` magic bytes after decoding, must use a plain `.pdf`
  basename, and is limited to 10 MiB. Base64 exists only in the mutable staging row;
  confirmation writes the exact decoded PDF bytes to the content-addressed store with
  media type `application/pdf`.
- `POST /api/episodes/{id}/review-imports/openreview` accepts only
  `{ "forum_id":"<public forum-or-root-note id>" }`. It performs one credential-free
  OpenReview API2 request to the fixed `https://api2.openreview.net` allowlisted host.
  Redirects and ambient proxies are disabled, timeout is 10 seconds, and the response
  is capped at 2 MiB. The exact UTF-8 JSON response is staged. A successful response has
  `fetch_performed:true` and the exact API URL in `source_ref`; it still has no object SHA.
- All staging responses include `needs_human_confirmation:true`. The generic endpoint
  deliberately rejects `source_kind:"openreview"`; public fetching always uses the
  dedicated endpoint and it accepts no credential or arbitrary-URL fields.
- `POST /api/review-imports/{id}/confirm` accepts
  `{ "actor", "parsed?", "redaction_confirmed":true, "training_consent", "license_basis" }`.
  Only this explicit one-shot redaction/license gate writes the original evidence bytes
  to the immutable object store and returns their SHA-256.

### Dataset snapshots

- `POST /api/dataset-snapshots/preview` accepts
  `{ "episode_ids":[], "require_training_consent":true }` and returns
  `eligible`, `excluded`, counts, and `selection_sha256`.
- `POST /api/dataset-snapshots` accepts the same selection plus `name`, `actor`,
  `license_basis`, and `expected_selection_sha256`. It fails if the selection changed
  or is empty. It never starts training.
- `GET /api/dataset-snapshots` -> `{ "items": [...] }`
- `GET /api/dataset-snapshots/{id}` returns manifest and members.
- `GET|POST /api/dataset-snapshots/{id}/verify` verifies manifest and every member.

Every eligible member freezes the verified candidate/rebuttal lineage, including the
condition, candidate artifact/record/input/prompt and binding-receipt hashes. Preview,
snapshot creation and later verification all re-check that lineage; consent, license,
redaction and revision integrity alone are not sufficient.

## Storage and immutability

Migration v5 adds:

- `research_episodes`, `episode_entity_links`
- `content_objects`
- `episode_revisions`, `episode_revision_objects`
- `team_intakes`, `review_import_batches`
- `dataset_snapshots`, `dataset_snapshot_members`

SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers reject changes to content objects,
episode revisions, revision-object membership, dataset snapshots, and snapshot members.
Cardinality triggers also reject late object/member insertion after the declared immutable
set is complete (`object_count` / `member_count`).
Objects live under `runtime/data-vault/objects/<sha-prefix>/<sha256>` and are verified
by byte length and SHA-256. Revision manifests also validate parent IDs, parent chain
hashes, object membership, and schema version.

Likely credentials are rejected before persistence. Ordinary research token-budget
descriptions remain valid; bearer tokens, API keys, passwords, and private keys do not.
