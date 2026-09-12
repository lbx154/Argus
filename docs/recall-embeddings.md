# Optional semantic embeddings for memory recall

Without project configuration, experience, Wiki and Skill recall uses
`lexical-hash-v1-256`. This is local lexical hashing, **not semantic embedding**,
and does not make network requests. HTTP embeddings are an explicit project
opt-in. They supply advisory similarity; canonical experience records and
current Markdown remain authoritative.

Configure the project **state directory**, not its worktree:

```python
from argus_skill.life.recall_embedding import save_embedding_config

save_embedding_config(project_state_root, {
    "enabled": True,
    "endpoint": "https://api.openai.com/v1/embeddings",
    "model": "text-embedding-3-small",
    "dimensions": 256,
    "credential_env": "ARGUS_RECALL_EMBEDDING_API_KEY",
    "daily_request_budget": 16,
    "daily_input_bytes_budget": 32768,
})
```

Provide the credential through that environment variable in the Argus process.
The configuration stores only its variable name. No key is accepted in a
configuration field, URL user information, or URL query. Non-loopback endpoints
require HTTPS. The adapter sends OpenAI-compatible `model`, `input`,
`encoding_format="float"`, and `dimensions`; `request_dimensions=False` omits
the last field for compatible services that do not support it. The configured
dimension must still equal the returned vector dimension.

`MemoryBundle`, ordinary failure-experience stores, experience search tools,
and Markdown knowledge recall construct this adapter from
`embedding/config.json`. Reopening a store reads current configuration. Changing
the endpoint, model or dimensions changes its hashed encoder identity and
rebuilds the derived index. Source edits update vectors; removal or retirement
removes documents from recall. Successful computations are cached by redacted
input hash and encoder identity, allowing a bounded indexing batch to continue
on a later recall without repurchasing completed computations.

Limits apply before or during every external request. One recall shares its
network batch budget across document indexing and query embedding:

| Limit | Default |
| --- | --- |
| Request timeout | 3 seconds |
| Network batch deadline | 5 seconds |
| Uncached requests per batch | 16 |
| Input per request | 8192 UTF-8 bytes |
| Response per request | 256 KiB |
| Project daily requests | 256 |
| Project daily input bytes | 1 MiB |
| Process HTTP workers | 2 |
| Computation cache | 1024 entries and 8 MiB of vector payload |

Daily budgets use UTC and atomically reserve requests/input bytes in
`embedding/usage.sqlite3`, shared by reopened stores and processes. Failed or
timed-out requests retain their reservation. These are request/input limits,
not estimates of dollar spend. The separately disposable
`embedding/cache.sqlite3` stores hashes and vectors, without source text or
credentials. Deleting that cache does not reset spend accounting. Obsolete
computation hashes may remain until bounded cache eviction; they are never
searchable documents or source authority.

Inputs over the byte limit are rejected rather than silently truncated. HTTP
errors, exhausted budgets, dimension mismatches, nonfinite values, zero vectors,
or invalid response identities fall back to current lexical recall. No HTTP
retry follows automatically. Index transactions do not span HTTP calls;
concurrent index changes fence out older vector preparation. Experience writes
release their canonical file lock before refreshing the index; retrieval
rechecks its source snapshot after external calls. Existing symlink aliases and
special files are rejected for configuration, index, cache and budget paths.
SQLite's final path open still has a filesystem replacement race; this is not
process isolation against a concurrently malicious filesystem writer. Provider error
bodies and credentials are excluded from logs and derived indices.

OpenAI's official [embedding guide](https://developers.openai.com/api/docs/guides/embeddings)
and [create-embedding reference](https://developers.openai.com/api/reference/resources/embeddings/methods/create)
describe the float response format, dimension reduction for third-generation
models, and 8192-token input limit. Argus uses a conservative UTF-8 byte bound
instead of adding a tokenizer dependency. Tests use a local HTTP fixture; real
provider availability and model billing require a separate authorized check.
