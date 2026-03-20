# Astar Island Strategy

This document captures the preferred long-term strategy for improving the Astar Island solver over future rounds.

## Strategy

### Learning Loop

Each round gives you feedback: your per-seed scores. This tells you how good your parameter estimation and simulation are, but not where you're wrong. So your main learning signal is:

- Compare your predictions against your own observations, using the viewport data you collected.
- Track your scores across rounds. If scores plateau, your model has a fidelity ceiling, not just a parameter estimation problem.
- If scores vary wildly across seeds within a round, your model handles some map topologies better than others.

### Query Strategy (Most Important Lever)

You have 50 queries across 5 seeds. The naive split is 10 per seed, but that's not optimal.

Repeated viewports are more valuable than pure coverage. You need distributions, not snapshots. If you query the same viewport on the same seed 3 times, you get 3 independent samples of what happens there, which is directly usable for parameter estimation. Querying 10 different spots once each tells you less about the underlying dynamics.

Recommended approach:

- Pick 2-3 high-information viewports per seed, centered on dense settlement clusters near faction boundaries.
- Query each of those viewports 2-3 times.
- Use the remaining budget to check isolated settlements or expansion corridors.

Cross-seed insight:

- All seeds share hidden parameters.
- If you can estimate winter severity from seed 0's observations, that applies to all seeds.
- You do not need to learn everything from every seed, so queries can be specialized.

### Model Priorities (Ranked)

1. Get the static cells right. This is free and covers most of the map. Parse initial states, classify ocean, mountain, and deep forest as locked. This alone beats uniform by a lot.
2. Build a basic forward simulator. Even with wrong parameters, a simulator that captures "settlements near food grow, isolated ones die" will beat heuristics. It does not need to be perfect at first.
3. Estimate expansion rate and winter severity first. These two parameters dominate the outcome. High expansion plus mild winter means the map fills with settlements. Low expansion plus harsh winter means lots of ruins. You can often infer these from a handful of observations.
4. Add conflict and trade mechanics later. These create second-order effects such as faction borders and port survival advantage. They matter more for moving from a decent score to a strong one than for getting off the floor.
5. Calibrate with Monte Carlo volume. More simulation runs mean smoother distributions and lower KL divergence. Once the model is decent, increasing runs is an efficient way to gain points.

### Round-over-Round Improvement

- Round 1: Submit static predictions plus uniform for dynamic cells. The score will be low but non-zero. Use all 50 queries to collect data and understand the mechanics.
- Rounds 2-3: Use a basic simulator with hand-tuned parameters. Compare predictions versus observations and adjust.
- Round 4 and later: Automate parameter estimation so the script can observe, fit, simulate, and submit. This is where the `1.05^round_number` multiplier starts to matter more.

### What Separates Good From Great

- Good: Correct static cells plus reasonable settlement survival predictions.
- Great: Accurate faction dynamics, including which settlements die based on hostile proximity, coastal access, and food supply.
- Elite: Modeling trade-network effects, forest reclamation timing, and expansion cascades where new settlements found further settlements.

## Mechanics Encoded In The Bayesian Model

- Ocean and mountains are treated as invariant terrain.
- Inland cells only keep floor-level port mass; meaningful port probability is coastal.
- Settlement priors increase with water access, greenery support, and nearby settlement/frontier access.
- Winter is modeled as collapse pressure, especially for weak or isolated settlements.
- Trade is modeled as a coastal survival and prosperity advantage centered on observed ports.
- Ruins branch between decay, forest reclaim, settlement rebuild, and coastal port restoration.
