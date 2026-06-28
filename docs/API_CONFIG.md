# Unified API Configuration

argus-skill keeps model and image API credentials in one private file, but the
file is **route-based** rather than "one URL/key for everything":

```text
~/.argus-skill/capabilities/model_api.json
```

This file is outside the repository and is written with mode `0600`. Source code,
paper artifacts, prompts, and sidecars should never contain raw API keys.

## Route model

Each route can use a different provider, base URL, API key, wire API, and model:

| Route | Used for |
|---|---|
| `engineer` | main coding/research execution model |
| `reviewer` | reviewer gate model |
| `planner` | planner / supervisor 推理 model |
| `text` | generic text-model fallback |
| `image` | image generation, e.g. `gpt-image-2` |
| `image_review` | vision/text model that reviews generated images |

Example vault shape:

```json
{
  "version": 2,
  "capabilities": {
    "model_api": {
      "routes": {
        "engineer": {
          "provider": "openai-main",
          "base_url": "https://text-provider.example/v1/",
          "wire_api": "responses",
          "api_key": "...",
          "model": "gpt-5.4-mini"
        },
        "reviewer": {
          "provider": "openai-review",
          "base_url": "https://review-provider.example/v1/",
          "wire_api": "responses",
          "api_key": "...",
          "model": "gpt-5.4"
        },
        "image": {
          "provider": "azure-image",
          "base_url": "https://image-provider.example/openai/v1/",
          "wire_api": "images",
          "api_key": "...",
          "model": "gpt-image-2"
        },
        "image_review": {
          "provider": "openai-vision",
          "base_url": "https://vision-provider.example/v1/",
          "wire_api": "responses",
          "api_key": "...",
          "model": "gpt-5.4"
        }
      }
    }
  }
}
```

## One-time setup for a single shared endpoint

```bash
export OPENAI_API_KEY="<your key>"
export OPENAI_BASE_URL="https://ai4m6.openai.azure.com/openai/v1/"
export ARGUS_SKILL_IMAGE_MODEL="gpt-image-2"
export ARGUS_SKILL_IMAGE_REVIEW_MODEL="gpt-5.4"

argus-skill --init-model-api
unset OPENAI_API_KEY
```

If provider settings already live in Codex config:

```bash
ARGUS_SKILL_CODEX_CONFIG=.codex/config.toml argus-skill --init-model-api
```

## One-time setup for split endpoints

Use route-specific env vars before importing:

```bash
export ARGUS_SKILL_ENGINEER_API_KEY="<text key>"
export ARGUS_SKILL_ENGINEER_BASE_URL="https://text-provider.example/v1/"
export ARGUS_SKILL_ENGINEER_MODEL="gpt-5.4-mini"

export ARGUS_SKILL_REVIEWER_API_KEY="<review key>"
export ARGUS_SKILL_REVIEWER_BASE_URL="https://review-provider.example/v1/"
export ARGUS_SKILL_REVIEWER_MODEL="gpt-5.4"

export ARGUS_SKILL_IMAGE_API_KEY="<image key>"
export ARGUS_SKILL_IMAGE_BASE_URL="https://image-provider.example/openai/v1/"
export ARGUS_SKILL_IMAGE_MODEL="gpt-image-2"
export ARGUS_SKILL_IMAGE_WIRE_API="images"

export ARGUS_SKILL_IMAGE_REVIEW_API_KEY="<vision key>"
export ARGUS_SKILL_IMAGE_REVIEW_BASE_URL="https://vision-provider.example/v1/"
export ARGUS_SKILL_IMAGE_REVIEW_MODEL="gpt-5.4"

argus-skill --init-model-api
```

You can also hand-edit the vault file directly. Keep it outside git and at mode
`0600`.

## Status check

```bash
argus-skill --model-api-status
```

The status output is secret-free. It reports each route's availability, provider,
base URL source, key source, wire API, and model.

## Runtime consumers

- `argus_skill.tools.image_tool` loads the `image` route for generation and the
  `image_review` route for vision review.
- `argus_skill.life.research_profile` exposes capability metadata to missions
  without exposing secrets.
- Daemon and REPL processes use the same vault path unless `--life-dir` or
  `ARGUS_SKILL_CAPABILITY_VAULT` overrides it.

## Security rules

- Do not commit `.env`, `.codex/`, or the vault file.
- Do not paste keys into paper prompts, generated figures, sidecars, or logs.
- Rotate the vault by re-running `argus-skill --init-model-api` with new
  environment variables.
