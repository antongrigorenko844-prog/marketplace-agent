# Archived one-time LEOPARTS actions

These exact-SKU Actions have completed their one-time role and are moved out of `.github/workflows/` so they are not accidentally triggered again.

The original YAML remains in Git history and in this folder for audit/rollback. Nothing in `.github/workflows-archive/` is automatically recognized as a GitHub Actions workflow. This migration did **not** disable recurring inventory, marketing, or order workflows.

Replacement for routine SKU image changes, Ozon/WB/Market inspection, Tilda CSV plan, Avito feed review and stock audit:
`.github/workflows/leoparts-operations-v2.yml`.

Archived on 23 Nov 2026 after verifying these files had no schedule or workflow_call triggers. 
