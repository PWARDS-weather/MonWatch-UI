import os
from .shared_models import (
    adapt_from_track_points, adapt_to_offsets
)
from . import algo_6_thinning
from . import algo_4_greedy
from . import algo_5_trackoffset
from . import algo_1_annealing
from . import algo_2_forcedirected
from . import algo_3_integerprog
from . import algo_7_leaderlines
from . import algo_8_anticlima
from . import algo_9_railway_bezier
from . import algo_10_8direction
from . import algo_11_staggered_perp
from . import algo_12_anticlima_v2


def count_overlaps(placed_labels, padding=4):
    count = 0
    for i in range(len(placed_labels)):
        for j in range(i + 1, len(placed_labels)):
            a = placed_labels[i]
            b = placed_labels[j]
            if (a.bbox_left + padding < b.bbox_right and
                a.bbox_right - padding > b.bbox_left and
                a.bbox_top + padding < b.bbox_bottom and
                a.bbox_bottom - padding > b.bbox_top):
                count += 1
    return count


def run_new_label_pipeline(track_points, canvas_w=1200, canvas_h=800,
                           px_p_deg_lon=100, px_p_deg_lat=100,
                           lon_min=0, lat_min=0, pts_p_px=1.0,
                           strategy="auto", **kwargs):
    std_points = adapt_from_track_points(track_points)

    strategy = os.getenv("LABEL_ALGO", strategy).lower()

    active_points = algo_6_thinning.filter(std_points)

    if strategy == "anneal":
        candidates = algo_1_annealing.place(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px
        )
    elif strategy == "force":
        candidates = algo_2_forcedirected.place(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px
        )
    elif strategy == "milp":
        candidates = algo_3_integerprog.solve(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px
        )
    elif strategy == "greedy":
        candidates = algo_4_greedy.place(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px
        )
    elif strategy == "offset":
        candidates = algo_5_trackoffset.place(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px
        )
    elif strategy == "anticlima":
        candidates = algo_8_anticlima.place(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px
        )
    elif strategy == "railway_bezier":
        candidates = algo_9_railway_bezier.place(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px,
            probability_circles=kwargs.get("probability_circles")
        )
    elif strategy == "8direction":
        candidates = algo_10_8direction.place(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px
        )
    elif strategy == "staggered_perp":
        candidates = algo_11_staggered_perp.place(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px
        )
    elif strategy == "anticlima_v2":
        candidates = algo_12_anticlima_v2.place(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px,
            probability_circles=kwargs.get("probability_circles")
        )
    elif strategy == "auto":
        n = len(active_points)
        if n <= 6:
            candidates = algo_3_integerprog.solve(
                active_points, px_p_deg_lon, px_p_deg_lat,
                lon_min, lat_min, pts_p_px
            )
        elif n <= 10:
            candidates = algo_1_annealing.place(
                active_points, px_p_deg_lon, px_p_deg_lat,
                lon_min, lat_min, pts_p_px
            )
        else:
            candidates = algo_4_greedy.place(
                active_points, px_p_deg_lon, px_p_deg_lat,
                lon_min, lat_min, pts_p_px
            )
        overlap_count = count_overlaps(candidates)
        if overlap_count > 2:
            force_candidates = algo_2_forcedirected.place(
                active_points, px_p_deg_lon, px_p_deg_lat,
                lon_min, lat_min, pts_p_px
            )
            if count_overlaps(force_candidates) < overlap_count:
                candidates = force_candidates
    else:
        candidates = algo_4_greedy.place(
            active_points, px_p_deg_lon, px_p_deg_lat,
            lon_min, lat_min, pts_p_px
        )

    candidates = algo_7_leaderlines.rescue(
        candidates, canvas_w, canvas_h
    )

    return adapt_to_offsets(candidates)
