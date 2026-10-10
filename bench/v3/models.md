# Token Harbor free models — probe 2026-10-07 (GET /v1/models: 66 models, 4 `:free`)

| Model | Tool use (Anthropic /v1/messages) | JSON planning | 600-word cited section | Notes | Role in ODAR deep mode |
|---|---|---|---|---|---|
| deepseek-v4-flash:free | yes (3.6 s) | clean JSON (3.9 s) | 508–604 words, 0 uncited sentences, ~19 s | fastest good writer | planner, reflect, writer (1st) |
| mimo-v2.5:free | yes (3.4 s) | clean JSON (2.7 s) | 604 words, ~40 s; 1 of 2 concurrent calls returned empty | reliable, mid speed | judge/extract (1st), fallback for others |
| mimo-v2.6-flash:free | yes (2.8 s) | JSON wrapped in ```json fences (parser strips) | 574–589 words, ~85–90 s | slow | last fallback |
| deepseek-v4.1-flash:free | yes (5.3 s) | JSON ok (1.4 s) | **empty text**: spends all max_tokens on hidden `thinking` blocks (2,000 tokens for a 200-word ask) | reasoning model | **excluded** (failed twice: recorded, moved on) |

Override routes with `ODAR_MODEL_ROUTES='{"writer": ["model:free", ...]}'`.
