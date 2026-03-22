# Astar Island Solver

## The Challenge

Astar Island is a machine learning competition where you predict the outcome of a black-box Norse civilisation simulator. A 40x40 grid runs for 50 simulated years — settlements grow, factions clash, trade routes form, and winters reshape the map. Your job: predict the probability distribution of 6 terrain classes for every cell.

**Key constraints:**
- 50 viewport queries total, shared across 5 map seeds
- Each query reveals at most 15x15 cells from one stochastic simulation run
- All 5 seeds share hidden parameters (expansion rate, winter severity, etc.)
- Scoring: entropy-weighted KL divergence — only dynamic cells matter, uncertain cells matter most
- Zero probabilities → infinite KL divergence. Floor everything at 0.01.

**Terrain classes:** Empty (0), Settlement (1), Port (2), Ruin (3), Forest (4), Mountain (5).

**Simulation phases (per year):** Growth → Conflict → Trade → Winter → Environment.

**API:** `https://api.ainm.no/astar-island` — see full docs at `app.ainm.no`.

## Architecture

The solver runs a two-phase observation + prediction pipeline:

1. **Fetch round** — get map seeds and initial terrain states
2. **Phase 1 observations** (20 queries) — 2 high-value windows per seed, each repeated twice for distribution sampling
3. **Provisional prediction** — Bayesian predictor estimates uncertainty to guide phase 2
4. **Phase 2 observations** (30 queries) — adaptive targeting of high-entropy regions
5. **Final prediction** — Monte Carlo simulator generates H×W×6 probability tensors
6. **Submit** — upload predictions for all 5 seeds

### Package Layout

| File | Purpose |
|---|---|
| [`cli.py`](cli.py) | Command-line entrypoints |
| [`client.py`](client.py) | REST API client with retry logic |
| [`config.py`](config.py) | Environment and dotenv loading |
| [`models.py`](models.py) | Typed domain models (Pydantic) |
| [`terrain.py`](terrain.py) | Terrain mapping, feature helpers, `SeedFeatureGrid` precomputation |
| [`planner.py`](planner.py) | Phase 1 and phase 2 observation planning |
| [`predictor.py`](predictor.py) | Bayesian predictor (used for planning + diagnostics) |
| [`simulator.py`](simulator.py) | Monte Carlo simulator (used for final submission) |
| [`calibration.py`](calibration.py) | Ground truth calibration pipeline |
| [`delivery.py`](delivery.py) | High-level fetch/observe/predict/submit orchestration |
| [`storage.py`](storage.py) | Artifact serialization helpers |
| [`backtest.py`](backtest.py) | Offline evaluation and parameter calibration |
| [`cloud.py`](cloud.py) | Optional GCS sync hooks |
| [`batch.py`](batch.py) | Placeholder for distributed Monte Carlo |

## How Predictions Work

### Monte Carlo Simulator (`simulator.py`)

The primary submission model. For each cell, it samples from calibrated transition distributions based on:
- Initial terrain type (plains, forest, settlement, port, ruin)
- Whether the cell is coastal
- Whether it's on the settlement frontier (adjacent to initial settlements)
- Distance to nearest settlement

200 independent draws per seed, aggregated into probability distributions.

**Calibration source:** Distributions were fitted against ground truth from completed rounds via the `/analysis` endpoint. Key empirical findings that drove the calibration:

| Initial Terrain | GT Distribution (avg across 4 rounds) |
|---|---|
| Plains | 76.7% empty, 16.6% settlement, 1.0% port, 1.6% ruin, 4.2% forest |
| Forest | 8.8% empty, 17.4% settlement, 1.0% port, 1.7% ruin, 71.2% forest |
| Settlement | 42.2% empty, 33.5% settlement, 0.5% port, 3.1% ruin, 20.7% forest |

### Bayesian Predictor (`predictor.py`)

Used for phase 2 planning and diagnostics. Multi-layer inference with hierarchical backoff:

1. Direct observation → empirical posterior from viewport samples
2. Exact/relaxed feature bucket matching across seeds
3. Round and cross-seed transition indexes
4. Nearest-family distance-weighted interpolation
5. Static prior fallback (ground-truth-calibrated)

### Ground Truth Calibration (`calibration.py`)

Fetches post-round analysis data from completed rounds and computes empirical transition distributions bucketed by terrain features. Used to:
- Calibrate the simulator's default transition distributions
- Validate the predictor's static priors
- Track model improvement across rounds

## Performance

Backtested scores (new model vs submitted predictions):

| Round | Old Score | New Score | Delta |
|---|---|---|---|
| 15 | 56.6 | **84.2** | +27.6 |
| 14 | 44.4 | **70.7** | +26.3 |
| 8 | 28.3 | **62.6** | +34.3 |
| 7 | 36.4 | **50.0** | +13.6 |

### Key Fixes (v2)

Three systematic biases were identified and corrected:

1. **Ruin over-prediction** — The model predicted 11-29% ruin where ground truth shows 1.6-3.1%. Settlements that die become empty (42%) or forest (21%), not ruins. Fixed across simulator, predictor priors, latent proxy shifts, and transition policy.

2. **Forest colonization under-prediction** — 17.4% of forests become settlements in ground truth, but the model only predicted 6.4%. Boosted forest frontier settlement rates in both simulator and predictor.

3. **Plains emptiness under-prediction** — 76.7% of plains stay empty, but the model predicted only 52.4%. Increased empty base probability and suppressed ruin/forest leakage on vacant cells.

## Configuration

Loaded from environment variables (and `src/ai_accounting_agent/.env`):

| Variable | Purpose | Default |
|---|---|---|
| `ASTAR_ISLAND_ACCESS_TOKEN` | Bearer token for API auth (required) | — |
| `ASTAR_ISLAND_BASE_URL` | API endpoint | `https://api.ainm.no/astar-island` |
| `ASTAR_ISLAND_DATA_DIR` | Artifact storage directory | `data/astar_island` |

## CLI Usage

```bash
# Full end-to-end delivery (fetch → observe → predict → submit)
uv run astar-island deliver-round --submit

# Step-by-step
uv run astar-island fetch-round --artifact-dir data/astar_island/my_round
uv run astar-island collect-observations --artifact-dir data/astar_island/my_round
uv run astar-island predict --artifact-dir data/astar_island/my_round
uv run astar-island submit --artifact-dir data/astar_island/my_round

# Backtest against completed rounds
uv run astar-island backtest
```

## Testing

```bash
uv run pytest src/astar_island/tests -q
```

## Remaining Improvement Opportunities

1. **Build a forward simulator** — Model the 50-year simulation with settlement interactions (growth, raiding, trade, winter collapse). The current cell-independent Monte Carlo cannot capture spatial correlations.

2. **Per-round parameter adaptation** — Use current-round observations more aggressively to shift distributions. Rounds vary significantly in expansion rate and winter severity.

3. **Smarter observation allocation** — Concentrate more queries on fewer seeds for robust parameter estimation, then apply shared parameters to all seeds.

4. **Calibration-driven priors** — Use the `calibration.py` pipeline to dynamically compute priors from all available ground truth rather than hardcoded values.

5. **Reduce floor waste** — The 0.01 floor on mountain class for non-mountain cells wastes ~1% probability. Consider asymmetric floors per terrain type.
