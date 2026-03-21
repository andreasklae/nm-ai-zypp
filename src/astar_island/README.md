# Astar Island Solver

This package contains the local-first Astar Island competition solver that lives alongside the Tripletex agent in this repo.

It is designed to:

- fetch live round metadata from the Astar Island API
- plan and execute observation queries under the shared 50-query budget
- turn the initial map plus observations into `H x W x 6` probability tensors
- submit predictions for all 5 seeds
- persist every step as resumable artifacts on disk

The current model is a **Bayesian heuristic predictor**, not a full simulator reimplementation. It combines static map priors, empirical viewport evidence, mechanic-aware latent variables, cross-seed family backoff, two-pass spatial smoothing, local influence maps, and adaptive transition policies driven by observed expansion pressure to produce robust probability distributions with safe probability flooring.

## What Is Implemented

The package currently provides:

- a typed REST client for the live Astar Island API
- a CLI entrypoint exposed as `astar-island`
- a resumable two-phase observation workflow
- a mechanics-aware Bayesian prediction model with precomputed terrain feature grids
- cross-seed observation pooling for improved fallback predictions
- two-pass spatial smoothing with adaptive strength
- adaptive transition policy that scales forest/settlement suppression based on observed expansion pressure
- round-specific empirical transition rates used as a strong prediction component
- automatic parameter calibration in live delivery (no longer uses defaults)
- random perturbation parameter calibration on holdout data
- artifact persistence for round metadata, observations, predictions, manifests, and submission receipts
- optional Google Cloud inspection and GCS sync hooks
- offline unit tests plus opt-in live API smoke tests

The current prediction model name is:

- `hierarchical-empirical-v5-adaptive`

## Package Layout

- [`cli.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/cli.py): command-line entrypoints
- [`client.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/client.py): live API client with retry logic
- [`config.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/config.py): environment and dotenv loading
- [`models.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/models.py): typed domain models for rounds, observations, predictions, manifests, and diagnostics
- [`terrain.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/terrain.py): terrain mapping, handcrafted feature helpers, and `SeedFeatureGrid` precomputation
- [`planner.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/planner.py): phase 1 and phase 2 observation planning
- [`predictor.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/predictor.py): Bayesian predictor implementation
- [`delivery.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/delivery.py): high-level fetch/observe/predict/submit orchestration
- [`storage.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/storage.py): artifact serialization helpers
- [`cloud.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/cloud.py): optional `gcloud` and GCS helpers
- [`batch.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/batch.py): placeholder seam for future batch Monte Carlo execution
- [`STRATEGY.md`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/STRATEGY.md): long-term modeling strategy notes

## Runtime Configuration

Configuration is loaded from:

- process environment variables
- plus the repo-local dotenv file at [`src/ai_accounting_agent/.env`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/ai_accounting_agent/.env)

Supported settings:

- `ASTAR_ISLAND_ACCESS_TOKEN`: required for live API calls
- `ASTAR_ISLAND_BASE_URL`: defaults to `https://api.ainm.no/astar-island`
- `ASTAR_ISLAND_DATA_DIR`: defaults to `data/astar_island`
- `ASTAR_ISLAND_GCS_BUCKET`: optional artifact sync target
- `ASTAR_ISLAND_GCS_PREFIX`: optional GCS prefix

Important note:

- the access token should be treated like a secret and should not be committed to git

## CLI Commands

The package exposes one CLI script:

```bash
uv run astar-island --help
```

Available commands:

- `fetch-round`
- `collect-observations`
- `predict`
- `submit`
- `deliver-round`
- `cloud-status`

Typical live round workflow:

```bash
uv run astar-island deliver-round --submit
```

Typical step-by-step workflow:

```bash
uv run astar-island fetch-round --artifact-dir data/astar_island/my_round
uv run astar-island collect-observations --artifact-dir data/astar_island/my_round
uv run astar-island predict --artifact-dir data/astar_island/my_round
uv run astar-island submit --artifact-dir data/astar_island/my_round
```

## End-To-End Flow

The high-level orchestration lives in [`delivery.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/delivery.py).

`deliver-round` does the following:

1. Load settings and require a live token.
2. Fetch the active round summary and full round detail.
3. Create an artifact directory for the round.
4. Save `round_summary.json`, `round_detail.json`, and `run_manifest.json`.
5. Execute phase 1 observations.
6. Build provisional predictions from phase 1 data.
7. Build and execute phase 2 observations.
8. Build final predictions for all seeds.
9. Validate tensor shape, normalization, and minimum probability floor.
10. Submit all seeds with resume-safe receipt tracking.

The orchestration is resumable:

- if a run is interrupted, `--force-resume` reuses the same artifact directory
- already-saved observation samples are reused
- already-submitted successful seeds are skipped during re-submit

## Artifact Format

Each live or offline run is stored in a round-specific artifact directory under `data/astar_island/` by default.

Artifacts include:

- `round_summary.json`
- `round_detail.json`
- `observation_plan.json`
- `observations.json`
- `predictions.json`
- `submission_receipts.json`
- `run_manifest.json`

The manifest records:

- active round metadata
- phase 1 and phase 2 plans
- current run status
- observation summaries
- latent proxy diagnostics
- uncertainty summaries
- prediction validation results
- submission receipts
- warnings and fallback notes

This makes the workflow audit-friendly and restartable.

## Live API Client

The live client is implemented in [`client.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/client.py).

Supported endpoints:

- `GET /rounds`
- `GET /rounds/{id}`
- `POST /simulate`
- `POST /submit`

Important implementation details:

- Bearer token auth is applied automatically when `ASTAR_ISLAND_ACCESS_TOKEN` is present
- `429` responses are retried with backoff
- repeated `429` failures are surfaced as a `RuntimeError` with the response body included
- round ids are treated as `str | int` because live round ids are UUID strings

## Observation Strategy

The observation planner is implemented in [`planner.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/planner.py).

The solver uses a two-phase plan.

### Phase 1

Phase 1 spends exactly 20 queries:

- 4 queries per seed
- 2 non-overlapping windows per seed
- each window is repeated twice

The intent is not pure map coverage. It is to collect repeated samples from high-value regions so the model sees distributions rather than single outcomes.

Phase 1 window scoring favors:

- settlement clusters
- coastal settlement corridors
- likely trade regions
- winter-risk inland settlements
- frontier cells
- ruin and forest reclaim areas

### Phase 2

Phase 2 targets the remaining 30 queries.

The planner:

- guarantees each seed at least 2 phase 2 queries
- uses the rest as repeated samples on the strongest diagnostic windows
- caps any single window at 4 repeats

Phase 2 scoring combines:

- prediction entropy
- under-sampling penalties
- settlement density
- trade hotspot hints from observed port stats
- winter stress hints from low food and low defense observations
- conflict hints from mixed `owner_id` observations
- ruin/forest reclaim branching

This matches the current strategy document: repeated viewports are treated as more valuable than naive one-time coverage.

## Prediction Model

The predictor lives in [`predictor.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/predictor.py).

The core flow is:

1. Build a static prior from the initial map.
2. Aggregate observed cell class frequencies from viewport queries.
3. Aggregate observed settlement stats from viewport query metadata.
4. Derive round-level latent proxies.
5. Build per-seed local influence maps.
6. Apply Bayesian backoff:
   - direct empirical posterior
   - exact feature bucket posterior
   - relaxed feature bucket posterior
   - nearest family posterior
   - prior fallback
7. Smooth non-static cells spatially.
8. Re-apply mechanic restrictions.
9. Floor and normalize every cell.

### Static Priors

The initial prior is mechanic-aware rather than uniform.

It uses:

- ocean and mountain invariants
- coastal adjacency
- nearby ocean score
- nearby forest score
- settlement density
- distance to nearest initial settlement
- frontier score
- mountain adjacency penalty
- ruin rebuild support score

The prior is designed to capture the challenge rules we currently trust:

- ocean and mountains are static
- ports are coastal
- settlements prefer water and greenery support
- forests are sticky and reclaim ruins
- ruins can branch into empty, ruin, forest, settlement, or coastal port

### Empirical Observation Layer

Each observed cell accumulates class counts over repeated viewport samples.

For observed cells, the model uses a Dirichlet-style smoothed posterior:

- empirical counts dominate
- the prior still contributes as regularization

This lets repeated observations push the model sharply without allowing literal zero probabilities.

### Feature Backoff

For unobserved cells, the model uses hierarchical backoff by handcrafted feature buckets.

Feature parts include:

- terrain group
- coastal flag
- distance-to-settlement band
- local settlement density band
- frontier flag
- occupied flag

The predictor tries:

1. exact bucket match
2. progressively relaxed bucket matches
3. nearest same-family observations
4. prior fallback

This is what makes the model Bayesian rather than purely rule-based.

### Observed Settlement Stats

The solver also uses `SimulationResult.settlements` when present.

It aggregates repeated observations for settlement positions using:

- `alive`
- `has_port`
- `owner_id`
- `population`
- `food`
- `wealth`
- `defense`
- `tech_level`

Numeric values are normalized round-locally with a neutral fallback when data is sparse.

### Latent Proxies

The round-level latent summary currently includes:

- `settlement_survival`
- `ruin_intensity`
- `port_prevalence`
- `expansion_pressure`
- `reclamation_rate`
- `winter_severity`
- `trade_strength`
- `conflict_pressure`
- `rebuild_strength`

These are not hidden parameters in the challenge’s native simulator. They are learned proxy variables inferred from the observation set, used to shift priors in a mechanically meaningful way.

### Local Influence Maps

Before blending and again before the final restriction pass, the predictor computes several local influence maps:

- `support_map`
- `winter_risk_map`
- `trade_map`
- `rebuild_map`
- `conflict_map`

These maps are driven by both terrain features and observed settlement stats.

They let the model express beliefs like:

- low-food / low-defense settlements imply nearby collapse pressure
- prosperous coastal ports improve nearby survival and port outcomes
- mixed `owner_id` neighborhoods raise frontier ruin risk
- strong nearby settlements increase ruin rebuild probability

### Transition Policy

After blending and after smoothing, the predictor applies a floor-safe restriction policy.

This is critical because smoothing alone can otherwise leak implausible mass into forbidden states.

Examples:

- ocean is forced back to class 0
- mountain is forced back to class 5
- inland cells get only floor-level port mass
- generic empty/plains cells suppress forest unless strong evidence says otherwise
- forests suppress settlement/port outcomes and reinforce forest persistence
- ruins are allowed to split into rebuild, reclaim, or decay outcomes

### Smoothing

Spatial smoothing is only applied to non-static cells.

The strength of smoothing depends on:

- local dynamic strength
- local support
- local trade influence

This keeps neighboring dynamic cells correlated while avoiding blur over ocean and mountain cells.

### Probability Safety

Every prediction cell is always:

- length 6
- normalized to sum to 1.0
- floored at `0.01`

This protects against catastrophic KL divergence blowups from zero probabilities.

## Terrain Helpers

The terrain and feature functions live in [`terrain.py`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/terrain.py).

Helpers include:

- terrain-code to prediction-class mapping
- coastal land detection
- nearby ocean score
- nearby forest score
- adjacent forest count
- adjacent mountain count
- settlement density
- distance to nearest settlement
- frontier score
- combined settlement support score
- ruin rebuild support score
- viewport coordinate clamping
- `SeedFeatureGrid` precomputation (caches all per-cell features once per seed)
- `prediction_entropy` and `normalize_metric` (shared utilities for planner and predictor)

These helpers intentionally keep the feature engineering readable and deterministic. All per-cell features are precomputed into a `SeedFeatureGrid` at prediction time, eliminating redundant neighborhood scans and providing a ~5.8x speedup.

## Submission Safety And Fallback Behavior

The delivery flow is designed to avoid losing a round due to partial failure.

Implemented protections include:

- prediction tensor validation before any submit
- resume-safe submission receipts
- skipping already successful seeds on retry
- phase checkpoints after phase 1 and after full observation collection
- fallback to prediction-only if the API clearly reports query budget exhaustion

In practice, one live edge case is worth knowing:

- if the API starts returning repeated `429` responses during live observation collection, the client retry loop can spend time waiting before surfacing the final error

Even in that case, phase 1 artifacts are still usable, and the workflow can continue via:

```bash
uv run astar-island predict --artifact-dir <artifact_dir>
uv run astar-island submit --artifact-dir <artifact_dir>
```

## Google Cloud Hooks

Google Cloud support is intentionally light for now.

Implemented today:

- detect active `gcloud` account and project
- detect ADC presence
- compute GCS artifact URIs
- sync artifact directories to and from GCS
- define a local and GCP batch backend seam

Not implemented yet:

- real distributed Monte Carlo simulator jobs
- Cloud Run / Batch worker execution for forward simulation

This is a future extension point, not part of the current live competition path.

## Testing

Tests live under [`tests/`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/astar_island/tests).

Coverage areas include:

- terrain mapping and distribution flooring
- config loading
- artifact serialization
- planner budget behavior
- predictor Bayesian behavior
- delivery and fallback logic
- live API smoke tests behind opt-in environment flags

Typical test command:

```bash
uv run pytest src/astar_island/tests -q
```

Broader repo validation:

```bash
uv run pytest src/ai_accounting_agent/tests src/astar_island/tests -q
```

Live tests are guarded and do not run unless explicitly enabled.

## Known Limitations

This implementation is intentionally not a full simulator.

Current limitations:

- no year-by-year forward simulation loop
- no explicit longship or naval movement model
- no explicit trade network graph simulation
- no explicit faction graph or alliance model
- phase 2 planning can be computationally heavier on dense rounds because it evaluates many mechanic-rich candidate windows

The model is strongest when:

- static terrain matters
- repeated viewports expose local stochastic behavior
- settlement stats are available in observation responses

It is weaker than a true forward simulator in cases where long-range causal chains dominate the final outcome.

## Why This Design

This solver is built around the practical constraints of the challenge:

- only 50 observation queries
- stochastic simulation outcomes
- shared hidden parameters across all seeds
- severe penalties for overconfident wrong probabilities

That pushes the implementation toward:

- repeated high-value queries
- conservative but informed priors
- Bayesian smoothing instead of hard classification
- resumable artifact workflows
- safe submission behavior

In short:

- the planner tries to spend the limited queries where they reveal the most about mechanics
- the predictor turns those observations into a stable probability tensor without pretending it has a full simulator
- the delivery layer makes sure we still get a valid submission even when the live API behaves badly or the shared budget disappears mid-run

## Suggested Next Steps

The most valuable future upgrades are:

1. Add a lightweight forward simulator behind the existing predictor interface.
2. Fit hidden parameters against repeated observations instead of relying only on proxy variables.
3. Use the batch/GCP seam for high-volume Monte Carlo sampling once the simulator exists.
4. Improve dense-round phase 2 planning performance.
5. Add richer live diagnostics so phase 1-only fallback can happen automatically and faster when the API budget is already gone.
