# Command Recipes

Common ContentFactory Feishu release commands. Replace `<root>` with the outputs root, usually `/Users/hui/Documents/ContentFactoryVault/04-Outputs`.

## Inspect Only

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root <root> \
  --count 5 \
  --mode inspect \
  --risk-policy conservative
```

## Prepare

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root <root> \
  --count 5 \
  --mode prepare \
  --risk-policy conservative \
  --run-id <run_id>
```

## Guarded

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root <root> \
  --count 5 \
  --mode guarded \
  --risk-policy conservative \
  --allow-permission-skip \
  --run-id <run_id>
```

## Execute Preflight

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root <root> \
  --mode execute \
  --confirm-run-id <guarded_run_id> \
  --allow-permission-skip \
  --preflight-only
```

## Execute

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root <root> \
  --mode execute \
  --confirm-run-id <guarded_run_id> \
  --allow-permission-skip
```

