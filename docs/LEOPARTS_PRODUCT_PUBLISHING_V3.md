# LEOPARTS Product Publishing v3 — first audited implementation (23.11.2026)

## Scope and truthfulness

This is a **safe migration slice**: one batch, one run, one report; planning is offline,
live mode reads APIs only. **It does not publish new products yet.** The existing
legacy pipeline is not removed or replaced. Do not claim complete autonomous
publishing until the guarded writer adapters, isolated pilot and rollback have
passed acceptance tests.

The workflow is `.github/workflows/product-publishing-v3.yml`, the CLI is
`scripts/product_publishing_v3.py`; regression coverage is
`tests/test_product_publishing_v3.py`.

## Measured audit: repository and 23 November five-SKU incident

Repository inspection on 23.11.2026 found **229 active YAML workflows** in
`.github/workflows/`, **45** with `20261123` in their file path,
and approximately **1,976** recorded GitHub Actions runs.
These are inventory/count measurements, not a claim that every workflow
runs every day or that every action is part of catalog publication.

Relevant concrete observations:

1. `product_intake_v2.py` has separate WB, Market, Tilda and read-only Ozon
   functions. Ozon creation was performed by bespoke SKU scripts.
2. `product-intake-v2-safe-review.yml` loops through **every** approved
   request on each single request-file change, even for unrelated batches.
   It commits status JSON back to Git.
3. The legacy `marketplace-agent` concurrency group is shared by many
   workflows, including the 3-hour inventory cycle and several diagnostic
   workflows. A read-only check can therefore queue behind a seller update
   and vice versa. v3 uses a separate per-batch group and has no writes.
4. `run-command.yml` checks out code and installs all requirements afresh.
   Many incident-specific workflows repeat the same setup and then
   `save_data.sh` commits results into `main`. The large number of
   setup/commit cycles is additional latency and maintenance overhead.
5. The five-SKU incident required separate Ozon category probes, two TCM
   imports, Gen2 moderation correction, two toolkit imports and multiple
   checks. An HTTP 200 / task_id was insufficient to establish a sale.
6. There is no completed automatic ChatGPT-Library-to-GitHub-binary bridge.
   The owner's three exact 6T30 originals were eventually transferred using
   an ad-hoc route. Future processes must verify **actual SHA256 bytes**.
7. There are several price/stock sources (approved requests, master, derived
   marketplace catalog, seller APIs and special overrides). Treating all as
   interchangeable can overwrite legitimate merchant edits.

### Verified five-product acceptance fixture

`data/leoparts_ozon_five_live_readback_20261123.json` reports a genuine
Ozon readback at 2026-11-23 01:17:45 UTC: five unique seller matches, five
created/approved, five `Продаётся`, five matching prices, and positive FBS
stocks of 20, 3, 3, 3 and 3. That evidence is a *historical snapshot*;
it must never be reused as a fresh live status for a new batch.

For context, the last two toolkit import Action runs took **207 seconds**;
the five-product Ozon live-readback workflow took **35 seconds**.
Neither represents the end-to-end time from a user message to publication
on all platforms, and Ozon/WB moderation is outside the engine's control.

## v3 architecture (currently delivered: PREPARE + OBSERVE)

```text
Owner-approved batch JSON
    |
    v
schema validation + immutable approved prices + SKU uniqueness
    |
    +-- local SHA256 media validation with (path,hash) cache
    |      external URL: approved but explicitly UNVERIFIED until read
    |      ChatGPT Library: needs explicit verified binary bridge
    |
    +-- offline per-SKU/per-channel plan (no API keys)
    |
    +-- live-only parallel read adapters (separate providers)
             | Ozon: list/info/prices/stocks once per batch
             | WB: list cards/prices once per batch
             | Market: list catalog + ONE batched price read
             | Tilda: read public catalog
             | Avito: disabled while auto-upload subscription unpaid
             v
       one JSON report + GitHub Job Summary + ephemeral Action artifact
       (no commits to master, prices, stocks, ads or seller account)
```

The **unit of state is (batch_id, SKU, channel)**. The report preserves each
independent result; a failed channel becomes `api_error`, never an across-
the-board failure or a false `selling` assertion.

`selling` is currently asserted **only for Ozon** when all conditions
match: exact SKU, unique seller match, `is_created`, moderation approved,
correct price, positive FBS balance, and seller status `Продаётся`.
For WB, Yandex and Tilda the statuses deliberately say
`catalog_verified`, `business_catalog_verified` or
`public_catalog_verified`; these are not claims of retail availability.

A live run fetches read-only data in parallel for up to four independent
providers. It preserves a logical count of provider read phases rather than
inventing a precise count of underlying paginated HTTP calls.

## First-run operator instructions

GitHub → Actions → **LEOPARTS Product Publishing v3** → Run workflow.

- Batch ID: `leoparts-five-20261123` (or another approved request filename
  without `.json`)
- Mode `plan`: fast, offline, no credentials. Always safe.
- Mode `live`: reads seller catalogs and summarizes current status.
- Channels: defaults to `ozon,wildberries,yandex_market,tilda,avito`;
  Avito is explicitly **disabled** even when present in the list.

Download the single JSON artifact or read the GitHub run summary.

### One-command connected-agent path

If a GitHub-connected agent is already operating in the repository, it can
update **one stable file**: `data/product_intake_v3/dispatch.json`, with
`batch_id`, `mode: plan|live`, `channels` and an incremented `revision`.
The `main` push automatically triggers **one** v3 batch read-only workflow;
no manual GitHub Actions clicks and no per-SKU script or workflow creation.
The dispatch file cannot select `apply` or point to an arbitrary filesystem
path. To start another identical request, increment `revision`.
The resulting action artifact and Job Summary contain the entire batch status.
This is not yet a write-enabled publishing command.

**Never run a legacy one-off publishing workflow just to refresh status.**

Locally:

```shell
python scripts/product_publishing_v3.py --batch leoparts-five-20261123 --mode plan
python -m unittest discover -s tests -p 'test_product_publishing_v3.py' -v
```

## Source-of-truth policy for future guarded writers

| Fact | Authoritative source |
| --- | --- |
| Product identity and SKU | immutable approved intake request |
| Owner-approved initial channel prices | approved request; no price formula |
| Current operational stock | existing central inventory synchronizer, NOT stale request |
| New-product media | verified SHA256 of exact accepted original + stable asset URL |
| Category and required customs fields | per-item marketplace schema and backed exact-product evidence |
| Existence, moderation, sales eligibility | current marketplace API readback |
| Duplicate prevention | exact seller SKU lookup + recorded import task + platform ID |
| Avito enablement | paid auto-upload status and explicit activation, currently OFF |

Protect historic `master_control.xlsx`, `ozon_catalog.xlsx`, price overrides,
order journals and photos. Never use this v3 read-only path to change them.

## Prioritized next implementation slices

1. **P0 — done:** single multi-channel plan/live diagnostic, isolated
   concurrency, independent failure handling, no data writes, one artifact.
   PR unit tests cover five real SKU identities, Avito disabled, Ozon price
   rejection and batched reads.
2. **P1 — next:** generic asynchronous state machine + durable per-task
   idempotency keys, task IDs, structured error taxonomy, and scoped retries.
   Keep a registry with monotonic transitions. Do not create a new workflow
   per SKU.
3. **P1 — next:** guarded Ozon/WB/Market/Tilda writer adapters, each with
   isolated SKU+channel transactions, anti-duplicate preflight, approved
   category/TN VED requirement, post-write readback and stock ownership
   protection. Test against mocks and one approved pilot SKU first.
4. **P1 — next:** approved-photo bridge: attach from a supported connected
   source once, hash/persist bytes, publish content-addressed immutable
   links; never use a visually similar image as a fallback.
5. **P2:** move repeated legacy reporting into v3, remove only
   inventoried obsolete Actions after verifying no external caller or
   scheduled task depends on them. In particular, keep stock and order
   reconciliation independent, with its own serialized lock.
6. **P2:** introduce a durable operator dashboard/UI for a single batch.
   Distinguish `submitted`, `moderating`, `declined`,
   `created` and `selling`, exposing the precise missing field
   as the only owner question.

### Engineering targets (NOT measured outcomes)

For a new 1–30 SKU batch that already has approved content/media and
category/customs metadata: target **one initiation**, no per-SKU workflow,
**one independent task per (SKU, channel)**, offline planning under
**30 seconds**, provider dispatch under **3 minutes** in healthy APIs,
and automatically resumed checks until terminal states. These are targets,
not achieved service levels. Marketplace moderation cannot be accelerated
or guaranteed by our code.

For this first safe v3 slice: no active product writes, hence zero chance
of accidentally changing live prices/stock via this workflow. Accept the
trade-off that the production automatic writer is *not yet available*.

## Rollback

To stop using v3, stop invoking the new `product-publishing-v3.yml` workflow.
It has no scheduling trigger and no seller writes. The existing v2 process,
main marketplace jobs and their stock/price schedules are unchanged.


---

## v3 guarded publisher — safe implementation (23 November 2026)

Files:
- `scripts/product_publishing_v3_adapters.py`: one reusable adapter each for
  Ozon, WB, Yandex Market, Tilda.
- `scripts/product_publishing_v3_apply.py`: single SKU×channel coordinator,
  immutable owner approval, durable journal, resume and real status readback.
- `tests/test_product_publishing_v3_apply.py`: mocked crash/retry/approval
  tests, no seller secrets or writes.

**Do not confuse implemented write code with a verified production rollout.**
The write path must first pass CI, a real no-write five-product pilot, then a
separately approved *genuinely NEW* SKU pilot. Existing five products are
already active and must not be re-imported or treated as pilot new offers.

### Safety design

- No writes on `plan`, `live` or `pilot`.
- Actual apply requires `data/product_intake_v3/dispatch.json` to contain
  `mode=apply`, `apply_authorized=true`,
  `allow_existing_changes=false`, the exact SHA256 of the immutable
  approved manifest, and a channel allowlist.
- `apply` does **not** change any seller SKU already present before the
  request. A card hidden/archived is still considered an existing record.
- No Avito API calls until paid automatic listing subscription is confirmed.
- All currencies/prices are literal from the approved request. No automatic
  price formula or legacy repricing re-enabled.
- Never submit Ozon without an exact, explicitly supported
  category/type/TN VED plus source evidence; current Ozon required attributes
  are verified for each category. Never assume parts=tools.
- Each original photo is checked against owner-approved SHA256 before it
  becomes an input. Existing public URLs are verified; repository images are
  uploaded under a content-addressed filename.
- Operational inventory **must** already exist for the exact SKU and agree
  between `data/ozon_catalog.xlsx` and `data/master_control.xlsx`. If
  missing, the task is blocked before any new card is submitted. The writer
  never seeds the stock ledger or overwrites an existing quantity.
- For Tilda CommerceML, a same-ID import might overwrite a private draft
  unseen on the public storefront. Explicit evidence that the exact SKU is
  absent from the private catalog is required before submitting.
- WB prices and images are written only for a SKU created through this
  exact journal. New-channel creation is independent of existing WB cards.
- Each POST/PUT is preceded by a committed `intent`; after a crash,
  a pending intent is not automatically replayed. Resumption reads the
  platform and either recovers the existing SKU or blocks an uncertain
  outcome. Side-effect write intent is persisted before stock/price/media.
- Each `apply` step Git-commits the *single journal file*, not the entire
  data directory; commits use `[skip ci]`. It runs under
  `marketplace-agent` inventory serialization. Independent channel scans
  can run concurrently within the single worker.
- `data/product_intake_v3/state/<batch>.json` retains task and import IDs;
  transient moderation checks can resume through the same workflow.
  Scheduled resume only runs for pending tasks on an approved apply batch.
- Reports distinguish `done` only following readback. Ozon selling
  requires real seller `Продаётся` + approved moderation, price and positive
  stock. WB/Yandex/Tilda are **not** labeled 'selling' solely based on a
  catalog/price API response.

### One-command operational interface

Once a new manifest includes complete approved metadata/media and is present
in the repository:

1. Write one `data/product_intake_v2/requests/<batch-id>.json` file (the
   v2 manifest schema, not a second price source). SKU uniqueness, exact
   channel prices, stock and category evidence are mandatory.
2. Change **one** `data/product_intake_v3/dispatch.json` file with
   `batch_id`, desired `channels` and `mode=pilot` to check existence
   without a single seller mutation.
3. For an explicit owner-approved real publication only, set
   `mode=apply`, `apply_authorized=true`,
   `allow_existing_changes=false`, and
   `approved_manifest_sha256=<SHA256 of normalized JSON manifest>`.
   A connected GitHub agent can do these file changes directly, without
   sending the owner to GitHub Actions for each SKU.
4. The single workflow runs independent adapter operations and checks the
   platform responses. A single Job Summary and artifact show all SKU
   states; durable journal protects crash/restart. GitHub schedule only
   resumes *previously approved pending* batches.

**Existing inventory synchronization is not replaced by this release.**
Filling the exact canonical stock source for completely new SKU is a
separately guarded preparation step. Do not re-import the historical five
Ozon cards to test publishing.

### Connector-native media bridge

When a new owner-approved original is present in ChatGPT Library or a file
attachment but not in GitHub, a connected agent should use Files to obtain
the **actual image bytes**, hash them and stage an immutable blob using
GitHub's `create_blob(encoding="base64")`, then `create_tree`,
`create_commit` and optimistic `update_ref(expected_sha)` when its
tools actually expose the base64 bytes. The approved manifest then records
the exact path and SHA256.

**Current limitation:** Files may return only a protected image-asset pointer,
not bytes/base64. That pointer cannot be passed to GitHub's create_blob.
In such a session, do not claim the bridge worked or attempt to convert a
thumbnail/screenshot into an original. Use an already verified repository
asset or a legitimately accessible signed-download source; otherwise mark
that SKU's media as blocked and request the missing supported staging route.
Never substitute a visually similar Gen1/Gen2 image. GitHub runners cannot
directly access ChatGPT Library.

### Remaining release gate

Mocked end-to-end tests, live zero-write pilot on the previously approved five
SKU, then a *new authorized SKU* pilot are necessary before claiming fully
automatic merchant publication. If a real mandatory customs/category value
is absent, the single request should return the specific missing field and
continue unaffected channels; it must not invent tax/customs facts.

### Operational rollback

Revert the new guarded writer workflow/job or reset dispatch mode to `live`.
This does not remove old inventory/price/order workflows, and does not
roll back already created seller products. If external writes have occurred,
use seller task IDs in the journal to reconcile, not delete or archive cards
by guessing.
