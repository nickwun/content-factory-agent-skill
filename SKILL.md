---
name: content-factory-agent
description: Use when producing content factory drafts from pasted text or files using an Obsidian ContentFactoryVault, Prompt/Profile files, corpus summaries, Markdown outputs, metadata, cover prompts, and optional Feishu CLI publishing.
---

# Content Factory Agent

Use this skill to produce Markdown article drafts from pasted text or file attachments while using an Obsidian vault as the file repository.

Default vault path: `/Users/hui/Documents/ContentFactoryVault`.

This skill creates file-based content packages. It may publish prepared outputs to Feishu via Feishu CLI only when explicitly requested. It does not call WeChat APIs, replace the web workspace, or auto-publish without confirmation.

Content generation policy:

- Codex writes article text, title candidates, and cover prompts directly into the vault.
- Production scripts must not call OpenRouter or other external LLM APIs for article, title, cover prompt, or content-judgment generation.
- Internet material search and webpage fetching remain allowed for source-material collection.
- Feishu CLI/API calls remain allowed for explicitly requested publishing, image upload, read-only checks, and repair workflows.
- Missing title state must stop with `codex_title_required`; Codex must write `titles.md` and `metadata.json.titles`.
- Missing article text must stop with `codex_article_required`; Codex must write `article.md`.
- Missing cover image must stop with `codex_image_required`; Codex image generation must create `images/cover.png`.
- Scripts are responsible for validation, building publish Markdown, Feishu publishing, state, audit, reports, and backups.

## Core Workflow

1. If the user asks to normalize Word material, run `normalize-docx-material` first.
2. Identify processing mode:
   - `rewrite`: rewrite source material into a new article.
   - `translate_to_zh_article`: translate and organize source material into a natural Chinese article.
3. Resolve profile from `02-Profiles`.
4. Resolve corpus summaries from the selected profile and `03-Corpus`.
5. Read source material from pasted text, attachments, or `01-Materials`.
6. Generate Markdown output.
7. Generate output metadata.
8. Generate one standardized cover prompt.
9. Save files under `04-Outputs/YYYY-MM-DD-slug/`.

## Capability: normalize-docx-material

Use this when source material arrives as Word `.docx`.

Directory contract:

- Raw Word files: `01-Materials/docx-raw/`
- Clean Markdown outputs: `01-Materials/rewrite-sources/`

Run:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/normalize_docx_material.py \
  --vault /Users/hui/Documents/ContentFactoryVault
```

For one file:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/normalize_docx_material.py \
  --vault /Users/hui/Documents/ContentFactoryVault \
  --file /Users/hui/Documents/ContentFactoryVault/01-Materials/docx-raw/example.docx
```

Each `.docx` produces:

- `01-Materials/rewrite-sources/xxx.md`
- `01-Materials/rewrite-sources/xxx.cleaning-note.md`

The Markdown should keep readable content only:

- title
- headings
- paragraphs
- lists
- quotes
- image placeholders
- table placeholders for manual review

Remove or ignore:

- Word headers and footers
- page numbers
- comments
- fonts, sizes, colors
- decorative styles
- common author/source lines near the top
- WeChat follow prompts and footer chrome

Leading image policy:

- If an image appears before the first real body paragraph, treat it as a likely cover image and remove it from Markdown.
- Record the removed leading image in the cleaning note.
- Keep image placeholders that appear inside the body because they may explain the text.

If an image cannot be extracted, keep:

`[图片占位：原文此处有图片]`

If a table is found, keep:

`[表格占位：原文此处有表格，请人工复核]`

Do not generate articles during normalization. Stop after clean Markdown and cleaning note unless the user explicitly starts the next milestone.

### Source Registry

The main registry is:

`01-Materials/source-registry.json`

Before batch or single rewrite, check this registry first.

Supported statuses:

- `unused`
- `normalized`
- `processing`
- `used`
- `failed`
- `skipped`

Each record contains:

- `sourceId`
- `fileName`
- `rawPath`
- `normalizedPath`
- `contentHash`
- `title`
- `status`
- `usedAt`
- `usedByOutput`
- `profile`
- `corpus`
- `notes`

Scan or update registry:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/scan_docx_materials.py \
  --vault /Users/hui/Documents/ContentFactoryVault
```

Mark source usage:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/mark_source_usage.py \
  --vault /Users/hui/Documents/ContentFactoryVault \
  --source-id source-xxx \
  --status used \
  --output 04-Outputs/YYYY-MM-DD-slug \
  --profile profile-ahong-running-rewrite \
  --corpus corpus-ahong-running-style
```

Default selection rules:

- Prefer `normalized` material.
- `unused` material must be normalized before rewrite.
- Skip `used` by default.
- Reuse `used` only when the user explicitly says `allowReuse: true`.
- If generation starts, mark source as `processing`.
- If generation succeeds, mark source as `used`.
- If generation fails, mark source as `failed` or return it to `normalized` with a note.
- If a batch is interrupted, inspect and manually reset `processing` records before continuing.

### Cleaning Note Requirements

The cleaning note must record:

- original file path
- output Markdown path
- extracted title
- whether images were found
- whether tables were found
- whether author/source lines were cleaned
- whether a leading cover image placeholder was cleaned
- whether WeChat footer chrome was cleaned
- whether manual review is needed
- whether the material can enter rewrite workflow

## Input Specification

Accept one of:

- Pasted text in the chat.
- Attached `.txt`, `.md`, or `.docx` files.
- Existing files in `00-Inbox` or `01-Materials`.

When `.docx` is provided, normalize it to Markdown before rewrite.

For Word-based source material, use `source-registry.json` and source Markdown frontmatter as the source of truth for reuse prevention.

Required user intent:

- processing mode, if not obvious.
- profile name or profile path, unless the user asks Codex to choose.

Do not require a separate handwritten generation prompt when a profile is available.

## Profile Reading Rules

Profiles live in `02-Profiles/*.md`.

Read YAML frontmatter and these sections:

- `Prompt Template`
- `Output Rules`
- `Style Notes`
- `Corpus Usage`

Profile frontmatter may include:

```yaml
type: profile
profile_id: profile-example
name: Example
processing_modes:
  - rewrite
platform: wechat_article
default_corpus:
  - ../03-Corpus/example.md
```

Only use a profile if its `processing_modes` includes the requested mode.

## Corpus Reading Rules

Corpus files live in `03-Corpus/*.md`.

Read only summaries and short reusable patterns:

- `Tone`
- `Structure`
- `Length Hint`
- `Reusable Phrases`
- `Avoid`

Do not copy long source passages from corpus into outputs. Corpus is style and structure reference, not factual source.

## Single Output Specification

Create a folder:

`04-Outputs/YYYY-MM-DD-short-slug/`

Inside it create:

- `article.md`: final Markdown draft.
- `metadata.json`: output metadata using the output metadata template.
- `brief.md`: brief used for this generation.
- `titles.md`: two groups of title candidates.
- `cover-prompt.md`: standardized WeChat cover image prompt only, no generated image.

Do not create `image-plan.md` for new outputs. This workflow only plans one cover image.

Article requirements:

- Markdown only.
- Start with one `#` title.
- Use `##` for major sections.
- Preserve factual claims from source material.
- Do not fabricate quotes, data, or personal experiences.
- Do not expose internal source references such as “素材里提到”, “原文说”, “待重写素材”, “根据资料”, or “本文素材” in the final article.

## Batch Output Specification

For batch work, create:

`04-Outputs/YYYY-MM-DD-batch-name/`

Inside it create one subfolder per item:

- `001-short-slug/article.md`
- `001-short-slug/metadata.json`
- `001-short-slug/brief.md`
- `001-short-slug/titles.md`
- `001-short-slug/cover-prompt.md`

Also create:

- `batch-summary.md`

Batch summary must include:

- item count
- profile used
- corpus files used
- per-item title
- per-item status

## Metadata Specification

Use JSON:

```json
{
  "type": "output_metadata",
  "outputId": "output-",
  "title": "",
  "status": "draft",
  "processingMode": "rewrite",
  "targetPlatform": "wechat_article",
  "profileId": "",
  "corpusId": "",
  "sourceMaterials": [],
  "createdAt": "",
  "updatedAt": "",
  "images": {
    "cover": {
      "promptFile": "cover-prompt.md",
      "outputPath": "images/cover.png",
      "status": "prompt_ready",
      "ratio": "4:3"
    }
  },
  "titles": {
    "pain_point": [],
    "cognitive_gap": [],
    "recommended": {
      "primary": "",
      "secondary": "",
      "reason": ""
    }
  }
}
```

Do not include inline image plans. Future real image generation should only create `images/cover.png`.

Keep `status` as `draft` unless the user explicitly asks to archive or mark as published.

## Title Candidate Specification

Each output must include `titles.md` and `metadata.json.titles`.

`titles.md` format:

```markdown
# 标题候选

## 击中痛点型

1. ...
2. ...
3. ...
4. ...
5. ...

## 认知差型

1. ...
2. ...
3. ...
4. ...
5. ...

## 推荐首选

- 首选标题：
- 备选标题：
- 推荐理由：
```

Title rules:

- `pain_point` must contain exactly 5 titles that hit reader anxiety, confusion, cost, or “this is me” feeling.
- `cognitive_gap` must contain exactly 5 titles with counterintuitive framing or a useful perspective shift.
- Titles must stay grounded in article facts.
- Prefer the reference Word title formula: concrete running object + reader identity + conflict question + result/cost.
- Use concrete objects from the article first: distance, pace, frequency, age, stage, or runner group, such as 5 公里, 10 公里, 半马, 40 岁后, 普通跑者.
- Cover a varied formula mix when generating candidates:
  - Standard-test: “普通人跑完 X 公里，算什么水平？”
  - Misconception-correction: “很多人以为 X，其实 Y”
  - Numbered reminder: “40 岁后跑步，这 3 件事比配速更重要”
  - Comparison choice: “每天跑 5 公里，还是隔天跑 10 公里？”
  - Long-term result: “坚持跑步一年后，身体会发生什么？”
  - Age/stage reminder: “人到中年，跑步最怕的不是慢”
- Do not use clickbait.
- Do not exaggerate health risks.
- Do not use low-quality or fear-mongering words such as “震惊”, “崩溃”, “千万别”, “慢性自杀”, or “性命攸关”.
- Absorb the conflict and specificity of reference Word titles, but lower fear, shame, sexualized, gossip, and medical-absolutist framing.
- Rewrite scary titles as practical reminder titles, and rewrite novelty/gossip titles as ordinary-runner self-check titles.
- Do not make all titles use the same sentence pattern.
- Do not copy the original source title.
- Do not copy corpus titles or corpus sentences.

Validate title candidates for existing outputs:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/generate_titles.py \
  /Users/hui/Documents/ContentFactoryVault/04-Outputs/YYYY-MM-DD-slug
```

`generate_titles.py` no longer generates titles and no longer calls OpenRouter or any external LLM API. It only checks that `titles.md` and `metadata.json.titles` exist and are structurally complete.

If title state is missing or incomplete, it exits non-zero with status:

`codex_title_required`

When this happens, Codex must write `titles.md` and `metadata.json.titles` directly, then rerun validation. Do not use `--no-ai` fallback as a publishing-preparation path; the flag is accepted only for backward-compatible CLI parsing and does not generate titles.

## Feishu Publish Markdown

Use this before publishing to Feishu CLI. It prepares a single Markdown file that contains cover image, recommended title, title candidates, article body, and production info.

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/build_feishu_publish_markdown.py \
  /Users/hui/Documents/ContentFactoryVault/04-Outputs/YYYY-MM-DD-slug
```

This writes:

- `feishu-publish.md`
- `metadata.json.publish.feishu.status = prepared`
- `metadata.json.publish.feishu.markdownFile = feishu-publish.md`

It does not call Feishu CLI and does not create a Feishu document.

## Feishu CLI Publishing

Use this only after `feishu-publish.md` has been prepared and `quality.status` is `ready_for_edit`.

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/publish_to_feishu_cli.py \
  /Users/hui/Documents/ContentFactoryVault/04-Outputs/YYYY-MM-DD-slug
```

The script runs:

```bash
/Users/hui/.local/bin/feishu-cli doc import feishu-publish.md \
  --title "<recommended title>" \
  --upload-images \
  --verbose \
  -o json
```

Preflight requirements:

- `feishu-publish.md` exists.
- `metadata.json` exists.
- `publish.feishu.status` is `prepared`.
- `quality.status` is `ready_for_edit`.
- `images/cover.png` exists.
- `/Users/hui/.local/bin/feishu-cli` is executable.
- `FEISHU_OWNER_USER_ID` should be configured for automatic editor permission grant.

Authentication:

- Prefer environment variables `FEISHU_APP_ID` and `FEISHU_APP_SECRET`.
- If absent, the script may read local SQLite settings from `/Users/hui/Documents/distributing-web/data/content-agent.sqlite`.
- Do not write app secrets into scripts, Markdown outputs, reports, or command logs.

Editor permission grant:

- Configure one owner target. Prefer:
  - `FEISHU_OWNER_USER_ID`
- Other supported owner targets:
  - `FEISHU_OWNER_OPEN_ID`
  - `FEISHU_OWNER_UNION_ID`
  - `FEISHU_OWNER_EMAIL`
- Or pass one of these CLI flags. CLI flags override environment variables:
  - `--owner-user-id`
  - `--owner-open-id`
  - `--owner-union-id`
  - `--owner-email`
- After a successful `doc import`, the script runs `feishu-cli perm add <documentId> --doc-type docx --member-type <type> --member-id <id> --perm edit`.
- Single-output publishing may record `permission.status = skipped` if no owner is configured, but this is only acceptable for isolated permission probes.
- If permission grant fails, keep `publish.feishu.status = published`, write `publish.feishu.permission.status = failed`, and do not delete or republish the document.

Duplicate protection:

- If `publish.feishu.status` is `published` and `documentUrl` exists, the script refuses to publish again.
- Use `--force` only when intentionally creating a new Feishu document for the same output.

On success it writes:

- `metadata.json.publish.feishu.status = published`
- `documentId`
- `documentUrl`
- `publishedAt`
- `backend = feishu-cli`
- `permission.status`
- `permission.grantedTo`
- `permission.grantedAt`
- `publish-report.md`

On failure it writes:

- `metadata.json.publish.feishu.status = failed`
- `error`
- `publish-report.md`

## Small Batch Feishu CLI Publishing

Use this only after outputs are `quality.status = ready_for_edit` and have `article.md`, `titles.md`, `metadata.json`, and `images/cover.png`.

```bash
FEISHU_OWNER_USER_ID=<user_id> \
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/publish_feishu_batch.py \
  --root /Users/hui/Documents/ContentFactoryVault/04-Outputs \
  --limit 5 \
  --owner-user-id <user_id>
```

Batch rules:

- Serial execution only.
- Hard cap at 5 outputs per run.
- `FEISHU_OWNER_USER_ID` or an owner CLI flag is required by default.
- Batch publishing refuses to run when owner permission config is missing.
- Use `--allow-permission-skip` only for an intentional permission-skip probe; do not use it for normal batch publishing.
- Skip outputs that already have `publish.feishu.status = published` and `documentUrl`.
- Skip outputs whose `publish.feishu.review.status` is `ready_for_wechat`, `copied_to_wechat`, or `published_to_wechat`.
- Build `feishu-publish.md` first when missing.
- A single output failure must not stop the next output.
- Mark successful outputs as `publish.feishu.review.status = pending_review`.
- Write `batch-runs/YYYY-MM-DD-feishu-publish-batch-NN.md`.
- Do not modify `article.md`, `titles.md`, `images/cover.png`, or `source-registry.json`.

## Guarded Feishu Release Pipeline

Use this to orchestrate existing `04-Outputs` articles before a Feishu batch release. Version 1 never executes a real publish command; real publishing must remain a separate explicit command until a future `--execute` milestone exists.

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/run_feishu_release_pipeline.py \
  --root /Users/hui/Documents/ContentFactoryVault/04-Outputs \
  --count 5 \
  --mode inspect
```

Modes:

- `inspect`: scan only. It must not modify the vault, run quality checks, build `feishu-publish.md`, write run state, write summary files, or call Feishu.
- `prepare`: local preparation is allowed. It may run quality checks, build `feishu-publish.md`, and write run state, summary, Codex task notes, and backup manifests. It must not call real Feishu publish or upload images.
- `guarded`: includes `prepare`, then calls `publish_feishu_batch.py --dry-run` with explicit `--output-dir` arguments and prints the real publish command preview. It must not execute that command.

Candidate rules:

- Exclude already published outputs, outputs with `documentUrl`, `requiresRemoteCheck`, historical blocked state, risky slugs, missing article, missing cover, or not-ready quality.
- Risky slugs include `heart-risk`, `liver`, `drinking`, `red-flags`, and non-ASCII paths unless `--include-risky` is explicitly passed.
- Missing `titles.md` or `metadata.json.titles` is `codex_title_required`; the pipeline must not call OpenRouter, must not call `generate_titles.py --no-ai`, and must not generate fallback titles.
- If `--allow-title-fallback` is passed for compatibility, fallback output still cannot become publish-ready; it must stop as `needs_manual_title_review`.
- When Codex-authored title or article work is needed, write it directly into the output directory first, then rerun the pipeline.

Inspect diagnostics:

- `inspect` is a pure read-only diagnostic mode, but it must still output readiness gaps and Codex tasks.
- Missing `article.md` is `codex_article_required`.
- Missing `images/cover.png` is `codex_image_required`.
- Missing or incomplete `titles.md` / `metadata.json.titles` is `codex_title_required`.
- Missing `metadata.json.quality.status` is `quality_check_required`.
- `quality.status = needs_revision` or another non-ready value is `quality_revision_required`.
- Ready quality with missing `feishu-publish.md` is `feishu_publish_required`.
- Complete local publish state is `ready_for_guarded_dry_run`.
- `inspect` must not automatically fix any issue; it only reports `codexRequiredTasks`, `qualityRequired`, `revisionRequired`, `feishuPublishRequired`, `readyForPrepare`, `readyForGuardedDryRun`, `riskExcluded`, `blocked`, and `allUnpublishedDiagnostics`.

## Feishu Review Status

Use this after a Feishu document has been published to record manual review and downstream WeChat handling status.

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/mark_feishu_review_status.py \
  /Users/hui/Documents/ContentFactoryVault/04-Outputs/YYYY-MM-DD-slug \
  --status ready_for_wechat \
  --notes "飞书复制到公众号预览通过"
```

Supported `review.status` values:

- `pending_review`
- `ready_for_wechat`
- `needs_edit`
- `rejected`
- `copied_to_wechat`
- `published_to_wechat`

The script writes:

- `metadata.json.publish.feishu.review.status`
- `checkedAt`
- `result`
- `notes`
- `feishu-check.md`

It requires `publish.feishu.status = published` and does not modify article content, images, or source registry.

## Cover Prompt Specification

Cover prompts live in `cover-prompt.md`.

The cover prompt serves a WeChat Official Account cover image. It must use a fixed 4:3 output ratio and include:

- article title
- article theme
- core emotion
- visual subject
- scene
- color palette
- composition
- style
- negative elements
- output ratio

Cover prompts must avoid visual sameness. Do not default every article to “one Chinese middle-aged male runner in a morning park.” Choose visual subject, scene, color palette, composition, and style from the article theme.

If the cover contains people, they must fit the Chinese WeChat context: Chinese people / Asian Chinese faces. They may be male or female, young, middle-aged, or silver-haired depending on the article. Add Western faces, European/American-looking people, and non-Chinese runners to negative elements unless the article specifically requires otherwise.

Batch cover diversity is mandatory before Codex image generation:

- For a 5-article batch with people in covers, target a roughly balanced gender mix: `2 female / 3 male` or `3 female / 2 male`. Do not generate all-male or all-female batches unless every article clearly requires it, and record the reason.
- Match age to the article topic: young/new runner for beginner habits, middle-aged runner for 40+ or midlife themes, silver-haired runner for aging/longevity topics, mixed ages for community or comparison topics.
- Vary scenes across the batch. For 5 covers, aim for 5 distinct scenes; do not repeat the same “park track / morning jogger” setup more than once.
- Vary shot type and composition: close-up shoes/watch, half-body preparation, back view, side running shot, still life with running objects, group-vs-solo contrast, indoor recovery, city commute scene.
- Before generating images, create a quick mental or written cover diversity matrix with columns: title, gender, age, scene, composition, visual style, and why it fits the article.
- After generating images, visually check the batch. If gender, age, or scenes collapse into sameness, regenerate the weakest covers with more specific prompts.

Vary scenes by topic, for example:

- community track
- city street after work
- riverside greenway
- winter sidewalk
- home doorway changing shoes
- indoor stretching
- restrained health-check reminder scene
- race finish area
- rain-wet road
- quiet still life with running shoes, watch, towel, or thermos

Vary styles by topic, for example documentary photography, warm editorial illustration, magazine cover, minimal poster, soft film look, or flat illustration. Do not use the same style repeatedly across a batch.

Do not plan inline article illustrations. Do not generate images unless the user explicitly asks for real image generation in a later step.

For this ContentFactory workflow, real cover generation must use Codex direct image generation only:

- Use the built-in Codex image generation tool for `images/cover.png`.
- Do not call external image APIs, including baoyu-imagine, OpenRouter image providers, Gemini image APIs, Replicate, DashScope, Seedream, Jimeng, or other provider CLIs.
- Do not download images from the web or use stock/photo-source downloads as covers.
- Leave Codex-generated originals in `/Users/hui/.codex/generated_images/...` and copy/resize the selected image into the output directory.
- Normalize the final cover to a real `1200x900` PNG at:

`images/cover.png`

The helper script below no longer generates images through external providers. It only marks the output as needing Codex direct image generation and must not be treated as a finished cover step:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/generate_cover_image.py \
  /Users/hui/Documents/ContentFactoryVault/04-Outputs/YYYY-MM-DD-slug
```

After using Codex image generation, copy the selected generated PNG to `images/cover.png`, resize to `1200x900`, and update `metadata.json.images.cover`:

- `status: "generated"`
- `provider: "codex-imagegen"`
- `model: "codex-direct-image-generation"`
- `width: 1200`
- `height: 900`
- `sourcePath: "/Users/hui/.codex/generated_images/...png"`

Do not overwrite existing `images/cover.png` unless intentionally regenerating a cover. Back up the old cover first when replacing it.

Small batch cover generation supports either explicit directories or scanning one `04-Outputs` root:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/generate_cover_image.py \
  /path/to/output-a \
  /path/to/output-b

python3 /Users/hui/.codex/skills/content-factory-agent/scripts/generate_cover_image.py \
  --scan /Users/hui/Documents/ContentFactoryVault/04-Outputs \
  --limit 5
```

Batch mode skips outputs whose `images/cover.png` exists and whose metadata cover status is already `generated`, unless `--force` is provided. A failure in one output must not interrupt the rest of the batch. Each batch writes a summary under `04-Outputs/batch-runs/YYYY-MM-DD-cover-batch-NN.md`.

## Single Article Pipeline

Use this when the user asks to run one full production item from a registered unused Word source.

Run with an explicit source:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/run_single_article_pipeline.py \
  --source-id source-xxx
```

Or let the script pick the first `unused` source in `01-Materials/source-registry.json`:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/run_single_article_pipeline.py \
  --env-file /Users/hui/Documents/distributing-web/.env.local
```

The single pipeline performs:

1. Read `source-registry.json`.
2. Select one `unused` DOCX source.
3. Normalize DOCX to `01-Materials/rewrite-sources/*.md`.
4. Stop if the cleaning note says the material cannot enter rewrite.
5. Mark the source as `processing`.
6. Require Codex-authored `article.md`, `brief.md`, `metadata.json`, `cover-prompt.md`, and title state; production scripts do not call external LLM APIs for these.
7. Validate `titles.md` and `metadata.json.titles`. If missing, stop with `codex_title_required`.
8. Prepare the cover prompt and mark the output as requiring Codex direct image generation.
9. Use Codex image generation directly to create `images/cover.png`, then update cover metadata with `provider: codex-imagegen`.
10. Mark the source as `used` after article output and title candidates exist, even if Codex cover generation still needs to be completed manually.
11. Write `pipeline-summary.md` in the output directory.

Output directory naming:

- New pipeline outputs must use safe ASCII slugs only: `YYYY-MM-DD-lowercase-letters-digits-hyphens`.
- Do not use Chinese characters, Chinese punctuation, question marks, commas, spaces, or other symbols in output directory names.
- Keep the human-readable Chinese title in `metadata.json.title`.
- Write `metadata.json.slug` and `metadata.json.outputDir` for future scripts, imports, and backups.

State rules:

- Normalize failure: do not mark the source as `used`; record a failure note.
- Missing article text or disabled article generation: stop with `codex_article_required`; Codex must write `article.md`, `brief.md`, `metadata.json`, and `cover-prompt.md`.
- Missing title state: stop with `codex_title_required`; Codex must write `titles.md` and `metadata.json.titles`.
- Codex cover still pending after article success: mark source as `used`; record `metadata.images.cover.status = prompt_ready`.
- Full success: mark source as `used`; record `metadata.images.cover.status = generated` and `metadata.images.cover.provider = codex-imagegen`.

Quality gates before writing a successful article output:

- Article must start with a real `#` title, not a placeholder.
- Article must include at least three `01 / 02 / 03` modules.
- Article must be 1100-1300 Chinese characters excluding whitespace.
- Article must not expose internal source narration such as “素材里提到”, “原文说”, or “根据资料”.
- The script does not call OpenRouter or any external LLM API for article generation. If article text is missing, Codex must produce it directly in the vault before the pipeline can proceed.

This script is single-item only. Do not use it for batch pipeline work until a separate small-batch pipeline milestone exists.

## Small Batch Article Pipeline

Use this only after the single pipeline is stable. The current small-batch milestone is deliberately capped at 5 serial items.

Run with explicit sources:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/run_pipeline_batch.py \
  --source-id source-a \
  --source-id source-b \
  --source-id source-c
```

Or let the script choose up to 5 `unused` sources:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/run_pipeline_batch.py \
  --limit 3
```

Small batch rules:

- Serial only; no parallel execution.
- Maximum 3 items for the first batch validation.
- Select only `unused` sources by default.
- Do not use `used` sources.
- A single item failure must not interrupt later items.
- Each item still uses the single pipeline quality gates.
- Write one summary to `04-Outputs/batch-runs/YYYY-MM-DD-pipeline-batch-NN.md`.
- Summary must include source IDs, per-item status, output directory, article character count, cover status, errors, and final registry status counts.

## Output Quality Check

Use this after article and cover generation, before importing an output into the web workspace for editing.

Run one output:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/quality_check_output.py \
  /Users/hui/Documents/ContentFactoryVault/04-Outputs/YYYY-MM-DD-slug
```

Run several outputs:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/quality_check_output.py \
  /path/to/output-a \
  /path/to/output-b \
  /path/to/output-c
```

Or scan recent outputs:

```bash
python3 /Users/hui/.codex/skills/content-factory-agent/scripts/quality_check_output.py \
  --scan /Users/hui/Documents/ContentFactoryVault/04-Outputs \
  --limit 3
```

The quality check reads:

- `article.md`
- `metadata.json`
- `brief.md`
- `cover-prompt.md`
- `images/cover.png`

It writes:

- `quality-report.md` in each output directory.
- `metadata.json.quality`.
- A batch summary under `04-Outputs/batch-runs/YYYY-MM-DD-quality-batch-NN.md` when checking multiple outputs.

Quality status values:

- `ready_for_edit`: suitable for web workspace editing.
- `needs_revision`: fix or regenerate before editing.
- `rejected`: do not use without a rewrite.

Minimum checks:

- Article exists.
- Article length is 1100-1300 Chinese characters excluding whitespace.
- Article contains `01 / 02 / 03` module headings.
- Article does not contain internal source narration such as “素材里”, “原文说”, “根据资料”, or “待重写素材”.
- Article does not appear to copy long exact sentences from normalized source material.
- Article does not appear to copy corpus wording.
- `titles.md` exists.
- `metadata.json.titles` contains 5 pain point titles, 5 cognitive gap titles, and a recommended primary/secondary/reason.
- Metadata is complete enough for later import.
- `images/cover.png` exists.
- `metadata.images.cover.status` is `generated`.
- Title is not empty, too short, or a placeholder.

The quality check must not modify `article.md`, `brief.md`, `cover-prompt.md`, `images/cover.png`, or `source-registry.json`.

## Output Discipline

- Write generated files to the vault, not the repository.
- Keep WeChat and web publishing separate.
- Publish to Feishu only when the user explicitly requests a Feishu CLI workflow.
- If source material is insufficient, create a brief explaining what is missing instead of inventing.
