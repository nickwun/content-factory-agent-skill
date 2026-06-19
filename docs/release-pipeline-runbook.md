# Feishu Release Pipeline Runbook

This runbook fixes the operating contract for ContentFactory Feishu releases. Do not improvise commands from memory.

## Standard Flow

```text
inspect
-> Codex fills required content gaps
-> prepare
-> guarded
-> user confirms
-> execute --preflight-only
-> execute
-> post-publish audit
-> backup
```

Default behavior stops at `guarded`. Real publishing requires explicit user confirmation and a guarded run id.

## Step Commands

### 1. Inspect

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root <root> \
  --count 5 \
  --mode inspect \
  --risk-policy conservative
```

Allowed: read metadata, detect gaps, list candidates, list Codex tasks.

Not allowed: run quality checks, generate titles, build `feishu-publish.md`, call Feishu, upload images, write run state, write summaries, or modify the vault.

### 2. Codex Fills Required Content Gaps

Codex may write only the required content files for selected outputs:

- `article.md`
- `titles.md`
- `metadata.titles`
- `cover-prompt.md`
- `images/cover.png`

Codex must not call OpenRouter or any external LLM API for article, title, or cover-prompt generation.

### 3. Prepare

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root <root> \
  --count 5 \
  --mode prepare \
  --risk-policy conservative \
  --run-id <run_id>
```

Allowed: run local quality checks, build `feishu-publish.md`, write run state, write summary, write backup manifest.

Not allowed: call Feishu publish, create Feishu documents, upload images, or mark an output as published.

### 4. Guarded

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root <root> \
  --count 5 \
  --mode guarded \
  --risk-policy conservative \
  --allow-permission-skip \
  --run-id <guarded_run_id>
```

Allowed: perform prepare behavior, run `publish_feishu_batch.py --dry-run` with explicit `--output-dir` arguments, write guarded run state, and print the real execute command preview.

Not allowed: execute the real publish command, create Feishu documents, upload images, or write `documentUrl`.

### 5. Execute Preflight

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root <root> \
  --mode execute \
  --confirm-run-id <guarded_run_id> \
  --allow-permission-skip \
  --preflight-only
```

Allowed: read the guarded run and validate current local state.

Not allowed: call `publish_feishu_batch.py`, create Feishu documents, upload images, write execute run state, write execute summaries, write backup manifests, or modify the vault.

### 6. Execute

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root <root> \
  --mode execute \
  --confirm-run-id <guarded_run_id> \
  --allow-permission-skip
```

Allowed: call `publish_feishu_batch.py` with explicit guarded output dirs, write execute state, write summary, and write pre/post backup manifests.

Not allowed: rescan root to select articles, rely on `--count`, add `--check-blocks`, retry uncertain side-effecting steps blindly, or continue after `repair_required`.

### 7. Post-Publish Audit

Use read-only checks to compare metadata, publish reports, run state, batch summary, and Feishu blocks when needed. Do not overwrite historical reports. If repaired, add reconciliation notes.

### 8. Backup

Create a targeted backup containing published article state, publish reports, run state, batch summaries, and repair notes. Do not modify source files during backup.

## Blocking States

- `codex_title_required`: Codex must write `titles.md` and `metadata.titles`.
- `codex_article_required`: Codex must write `article.md`.
- `codex_image_required`: Codex must create `images/cover.png` using Codex-native image generation.
- `quality_check_required`: run local quality check after required Codex content exists.
- `quality_revision_required`: revise content before prepare can continue.
- `feishu_publish_required`: build `feishu-publish.md` after quality is ready.
- `ready_for_guarded_dry_run`: the output can enter guarded dry-run.
- `repair_required`: publish happened but image upload or another repairable state needs manual reconciliation.
- `requires_remote_check`: stop; inspect remote Feishu state and local metadata before any side-effecting retry.
- `already_published`: stop; do not duplicate publish.

## Incident Rules

- Image `0/1`: stop, mark `repair_required`, do not continue later outputs, and perform repair/reconciliation manually.
- Timeout: stop, require remote check, and do not blindly retry side-effecting operations.
- Already published: stop, do not duplicate publish.
- Missing owner: fail fast unless the user explicitly allowed `--allow-permission-skip`.

