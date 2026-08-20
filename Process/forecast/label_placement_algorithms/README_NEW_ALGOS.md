# Label Placement Algorithms - New Engine

## Overview

This package provides 7 new label placement algorithms for forecast track
labeling, plus a pipeline hub that orchestrates them.  The original label
engine is left completely untouched — all new code is additive.

## Toggle

### In-App (Overlay)

In the **Tracks** tab of the right panel, a **Label Algo** dropdown lets you
choose between:

- **Polar**       — original alternating offset
- **Bezier**      — original curve-normal offset
- **Smart Bezier** — original bezier with cramp-aware flip
- **Greedy**      — 8-position scoring + AABB collision (new)
- **Offset**       — bearing-perpendicular offset (new)
- **Force**        — Verlet integration spring-physics (new)
- **Anneal**       — simulated annealing (new)
- **MILP**         — backtracking / brute-force (new)
- **Auto**         — greedy first, fallback to offset then anneal (new)

You can also set the `LABEL_ALGO` environment variable before launch:

```powershell
$env:LABEL_ALGO = "anneal"
python launcher.py
```

### Subprocess (Static Forecast Map)

Select the algorithm in the **Tracks** tab dropdown — the same value is
forwarded as `--algorithm` to the subprocess.  Or pass it on the CLI:

```powershell
python Process/forecast/common.py --input data.json --output out.png --algorithm greedy
```

## Algorithm Details

| # | Algorithm file | How it works |
|---|----------------|--------------|
| 1 | `algo_1_annealing.py` | Simulated annealing: random label-position swaps with exponential cooling schedule |
| 2 | `algo_2_forcedirected.py` | Verlet integration: spring anchors + box-repulsion, 50 iterations, damping 0.85 |
| 3 | `algo_3_integerprog.py` | Backtracking search over 8 candidate positions (fallback for >10 labels) |
| 4 | `algo_4_greedy.py` | Scores 8 candidate positions per label using overlap penalty + distance cost |
| 5 | `algo_5_trackoffset.py` | Perpendicular offset from track bearing, alternating side by curvature |
| 6 | `algo_6_thinning.py` | Grid-bucket density filter — keeps max N points via round-robin per grid cell |
| 7 | `algo_7_leaderlines.py` | Radial scan for empty screen-space rectangles, returns polyline anchor coords |

## File Structure

```
Process/forecast/label_placement_algorithms/
├── __init__.py
├── shared_models.py          # Point/PlacedLabel adapters
├── algo_1_annealing.py
├── algo_2_forcedirected.py
├── algo_3_integerprog.py
├── algo_4_greedy.py
├── algo_5_trackoffset.py
├── algo_6_thinning.py
├── algo_7_leaderlines.py
├── integration_hub.py        # Pipeline orchestrator
└── README_NEW_ALGOS.md
```

## Constraints

- No existing functions were modified.
- The original `_compute_label_offsets`, `_compute_bezier_offsets`, and
  `_cascade_bezier_offsets` are 100% untouched.
- All new variable names use unique prefixes (`_new_`, `algo_`, etc.) to
  avoid any collision with existing globals.
- `scipy.optimize.milp` is tried in `algo_3_integerprog.py` if available;
  the fallback is pure-Python backtracking.
