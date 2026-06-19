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

## Low-Risk Inventory Check

```bash
python3 scripts/run_feishu_release_pipeline.py \
  --root /Users/hui/Documents/ContentFactoryVault/04-Outputs \
  --count 5 \
  --mode inspect \
  --risk-policy conservative \
  --run-id <inspect_run_id>
```

If inspect reports `insufficient_low_risk_inventory`, use this response:

```text
当前低风险可发布库存不足。
不要放宽风险策略。
下一步应生成新的低风险内容候选，等待用户确认选题后再进入写作和发布准备。
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
