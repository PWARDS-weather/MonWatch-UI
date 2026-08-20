# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: controllers/animation_controller.py
# Description: Multi-frame satellite imagery sequence playback, prefetching, and cache management (Himawari/GOES).
#
# Principal Developer: Zero (Prince Al Zhanjie B. Dela Rosa)
# Affiliation: PWARDS-weather
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# See also: https://www.apache.org/licenses/LICENSE-2.0
#
# Copyright (C) 2025-2026 PWARDS-weather (Pasacao Weather Atmospheric
# and Real-Time Data System)
#
# --- OPEN-SOURCE POLICY ---
# Redistribution or modification without formally notifying PWARDS-weather
# developers constitutes unauthorized use and violates the license terms.
# Developers must be notified via email or GitHub issue before any changes
# are distributed. See LICENSE file for complete terms.
# =============================================================================


import json
import re as _re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QPointF, QThread, Signal
from PySide6.QtGui import QImage, QPixmap

from src.core.engine_dispatcher import get_engine, get_products
from src.core.helpers import _get_nc_glob_pattern
from src.workers.core import AnimationPrefetchWorker


class AnimationController(QObject):
    """Animation sequence controller for satellite imagery playback.

    Manages frame loading, prefetching, and playback control for
    Himawari/GOES multi-frame animation sequences. Handles cache
    management and progress reporting during animation operations.

    Attributes:
        main_ui (MainUI): Reference to the main UI instance for state access.

    Signals:
        frame_changed (int): Emitted when animation frame index changes.
        playback_toggled (bool): Emitted when playback state changes.
        prefetch_finished (): Emitted when frame prefetching completes.

    Example:
        >>> controller = AnimationController(main_ui)
        >>> controller.frame_changed.connect(self.on_frame_update)
        >>> controller._load_animation_sequence(dates, band='13')
    """

    frame_changed = Signal(int)
    playback_toggled = Signal(bool)
    prefetch_finished = Signal()

    def __init__(self, main_ui):
        super().__init__(main_ui)
        self.main_ui = main_ui

    def _toggle_animation(self):

        """Toggle animation playback state.

        Switches between play and pause states. If no frames are
        loaded, displays a warning dialog. Updates timer state
        and UI controls to reflect new playback status.

        Returns:
            None

        Side Effects:
            - Emits playback_toggled signal
            - Updates play/pause button state
            - Starts or stops animation timer
        """
        frames = getattr(self.main_ui, '_anim_frames', [])
        has_ready = any(f is not None for f in frames) if frames else False
        if not has_ready:
            if getattr(self.main_ui, '_anim_timestamps', []):
                self.main_ui.log("No ready frames yet -- run 'Load / Prefetch Sequence' first.")
            else:
                self.main_ui.log("No animation sequence loaded -- use 'Load / Prefetch Sequence' first.")
            return

        if getattr(self.main_ui, '_anim_playing', False):
            self.main_ui._anim_playing = False
            if hasattr(self.main_ui, '_anim_timer'):
                self.main_ui._anim_timer.stop()
            if hasattr(self.main_ui, 'anim_play_btn'):
                self.main_ui.anim_play_btn.setText("\u25B6 Play")
            self.main_ui._sync_viewport_anim_bar()
            self.playback_toggled.emit(False)
        else:
            if not frames or frames[self.main_ui._anim_index] is None:
                for i, f in enumerate(frames):
                    if f is not None:
                        self.main_ui._anim_index = i
                        break
            if (self.main_ui._anim_crs_list and len(self.main_ui._anim_geotransform_list) > 0
                    and self.main_ui._anim_crs_list[0] is not None):
                self.main_ui.current_crs = self.main_ui._anim_crs_list[0]
                self.main_ui.current_geotransform = self.main_ui._anim_geotransform_list[0]
            self.main_ui._last_overlay_key = None
            self.main_ui._anim_playing = True
            if hasattr(self.main_ui, 'anim_play_btn'):
                self.main_ui.anim_play_btn.setText("\u23F8 Pause")
            if hasattr(self.main_ui, '_anim_timer'):
                interval = self._compute_anim_interval()
                self.main_ui._anim_timer.start(interval)
            self._display_anim_frame(self.main_ui._anim_index)
            self._apply_scene_overlays_to_animation()
            self.main_ui._sync_viewport_anim_bar()
            self.playback_toggled.emit(True)

    def _on_anim_frame_changed(self, val):
        frames = getattr(self.main_ui, '_anim_frames', [])
        if not frames or not (0 <= val < len(frames)):
            return
        was_playing = getattr(self.main_ui, '_anim_playing', False)
        if was_playing:
            self.main_ui._anim_playing = False
            if hasattr(self.main_ui, '_anim_timer'):
                self.main_ui._anim_timer.stop()
            if hasattr(self.main_ui, 'anim_play_btn'):
                self.main_ui.anim_play_btn.setText("\u25B6 Play")

        target = self._find_nearest_valid_frame(val)
        if target == -1:
            self.main_ui.log(f"No data available near frame {val+1}.")
            return
        self._display_anim_frame(target)
        self.main_ui._sync_viewport_anim_bar()

    def _on_anim_speed_changed(self, *args, **kwargs):
        sender = self.sender()
        if sender is getattr(self.main_ui, 'vab_speed', None) and hasattr(self.main_ui, 'anim_speed'):
            try:
                self.main_ui.anim_speed.blockSignals(True)
                self.main_ui.anim_speed.setCurrentText(sender.currentText())
                self.main_ui.anim_speed.blockSignals(False)
            except Exception:
                pass
        elif sender is getattr(self.main_ui, 'anim_speed', None) and hasattr(self.main_ui, 'vab_speed'):
            try:
                self.main_ui.vab_speed.blockSignals(True)
                self.main_ui.vab_speed.setCurrentText(sender.currentText())
                self.main_ui.vab_speed.blockSignals(False)
            except Exception:
                pass

        if getattr(self.main_ui, '_anim_playing', False) and hasattr(self.main_ui, '_anim_timer'):
            self.main_ui._anim_timer.stop()
            self.main_ui._anim_timer.start(self._compute_anim_interval())

    def _compute_anim_interval(self):
        base_fps = self.main_ui.settings.get("animation_fps", 5) if hasattr(self.main_ui, "settings") else 5

        speed_text = "1x"
        if hasattr(self.main_ui, 'vab_speed') and self.main_ui.vab_speed and self.main_ui.vab_speed.isVisible():
            speed_text = self.main_ui.vab_speed.currentText()
        elif hasattr(self.main_ui, 'anim_speed') and self.main_ui.anim_speed:
            speed_text = self.main_ui.anim_speed.currentText()
        mult_map = {"0.5x": 0.5, "1x": 1.0, "2x": 2.0, "4x": 4.0, "8x": 8.0}
        mult = mult_map.get(speed_text, 1.0)
        fps = max(0.5, base_fps * mult)
        return max(16, int(1000 / fps))

    def _generate_animation_timestamps(self):
        try:
            yf = int(self.main_ui.anim_from_year.currentText())
            mf = int(self.main_ui.anim_from_month.currentText())
            df = int(self.main_ui.anim_from_day.currentText())
            hf = int(self.main_ui.anim_from_hour.currentText())
            mif = int(self.main_ui.anim_from_minute.currentText())
            yt = int(self.main_ui.anim_to_year.currentText())
            mt = int(self.main_ui.anim_to_month.currentText())
            dt = int(self.main_ui.anim_to_day.currentText())
            ht = int(self.main_ui.anim_to_hour.currentText())
            mit = int(self.main_ui.anim_to_minute.currentText())
            dt_from = datetime(yf, mf, df, hf, mif)
            dt_to = datetime(yt, mt, dt, ht, mit)
            if dt_to < dt_from:
                dt_from, dt_to = dt_to, dt_from
            step_text = self.main_ui.anim_time_step.currentText()
            if self._is_geo_target_text(self.main_ui.anim_type_combo.currentText()) and self._geotarget_uses_rapid():
                step_text = "2.5 min"
            if "2.5 min" in step_text:
                delta = timedelta(minutes=2, seconds=30)
            elif "10 min" in step_text:
                delta = timedelta(minutes=10)
            elif "30 min" in step_text:
                delta = timedelta(minutes=30)
            elif "1 hour" in step_text:
                delta = timedelta(hours=1)
            else:
                delta = timedelta(hours=3)

            times = []

            _MAX_SLOTS = 1500
            cur = dt_from
            while cur <= dt_to and len(times) < _MAX_SLOTS:
                times.append(cur.strftime("%Y_%m_%d_%H%M"))
                cur += delta
            if len(times) >= _MAX_SLOTS and cur <= dt_to:
                self.main_ui.log(f"Timeline capped at {_MAX_SLOTS} slots -- the selected range/step "
                                 f"would generate more. Increase the time step or shrink the range.")

            if times:
                self.main_ui.log(f"First generated slot: {times[0]} (exact user From time honored, no step alignment rounding applied to start)")
                self.main_ui.log(f"Generated {len(times)} slots from the exact From/To + step you selected. "
                                 f"'Max frames' only limits how many are pre-fetched into RAM (the timeline/slider will show all of them).")

            return times
        except Exception as e:
            self.main_ui.log(f"Timestamp generation error: {e}")
            return []

    def _sync_anim_dates_from_current(self):
        if not getattr(self.main_ui, "current_datetime", None):
            self.main_ui.log("Load a scene via the Scene tab first (date/time selectors).")
            return
        try:
            s = self.main_ui.current_datetime
            y, mo, d, hm = s.split("_")
            h = hm[:2]
            mi = hm[2:] if len(hm) >= 4 else "00"

            self.main_ui.anim_from_year.setCurrentText(y)
            self.main_ui.anim_from_month.setCurrentText(mo)
            self.main_ui.anim_from_day.setCurrentText(d)
            self.main_ui.anim_from_hour.setCurrentText(h)
            if mi in [f"{x:02d}" for x in range(0, 60, 10)]:
                self.main_ui.anim_from_minute.setCurrentText(mi)
            else:
                self.main_ui.anim_from_minute.setCurrentText("00")

            dt0 = datetime(int(y), int(mo), int(d), int(h), int(mi or 0))
            dt1 = dt0 + timedelta(hours=3)
            self.main_ui.anim_to_year.setCurrentText(str(dt1.year))
            self.main_ui.anim_to_month.setCurrentText(f"{dt1.month:02d}")
            self.main_ui.anim_to_day.setCurrentText(f"{dt1.day:02d}")
            self.main_ui.anim_to_hour.setCurrentText(f"{dt1.hour:02d}")
            m_rounded = (dt1.minute // 10) * 10
            self.main_ui.anim_to_minute.setCurrentText(f"{m_rounded:02d}")

            if hasattr(self.main_ui, "available_bands") and self.main_ui.available_bands and hasattr(self.main_ui, "_anim_selected_band"):
                avail = list(self.main_ui.available_bands)
                if "B13" in avail:
                    chosen = "B13"
                else:
                    chosen = avail[0]
                self.main_ui._anim_selected_band = chosen
                self.main_ui._anim_checked_bands = [chosen]
                self.main_ui._anim_active_content = ("BAND", chosen)
                if hasattr(self.main_ui, '_build_anim_content_grid'):
                    self.main_ui._build_anim_content_grid()
                if hasattr(self.main_ui, '_update_anim_checked_bands_label'):
                    self.main_ui._update_anim_checked_bands_label()
            self.main_ui.log("Animation dates synced from current scene (3-hour window). Adjust step / content / max frames then Load/Prefetch.")
        except Exception as e:
            self.main_ui.log(f"Sync dates error: {e}")

    def _sync_anim_sat_type_from_main(self):
        try:
            if hasattr(self.main_ui, 'sat_combo') and hasattr(self.main_ui, 'anim_sat_combo'):
                self.main_ui.anim_sat_combo.setCurrentText(self.main_ui.sat_combo.currentText())
            if hasattr(self.main_ui, 'type_combo') and hasattr(self.main_ui, 'anim_type_combo'):
                desired = self.main_ui.type_combo.currentText()

                if desired in [self.main_ui.anim_type_combo.itemText(i) for i in range(self.main_ui.anim_type_combo.count())]:
                    self.main_ui.anim_type_combo.setCurrentText(desired)
                else:
                    if self.main_ui.anim_type_combo.count() > 0:
                        self.main_ui.anim_type_combo.setCurrentIndex(0)
            self.main_ui.log("Animation satellite + type synced from main scene controls.")
        except Exception as e:
            self.main_ui.log(f"Sync sat/type error: {e}")

    def _cancel_anim_prefetch(self):

        """Cancel ongoing animation prefetch.

        Stops background prefetching threads and cleans up
        worker resources. Called when user changes animation
        parameters or closes the application.

        Side Effects:
            - Terminates prefetch worker threads
            - Clears prefetch queue
            - Resets prefetch progress indicators
        """
        worker = getattr(self.main_ui, "_anim_prefetch_worker", None)
        if worker:
            try:
                worker.cancel()
            except (RuntimeError, AttributeError):
                pass

        thread = getattr(self.main_ui, "_anim_prefetch_thread", None)
        if thread:
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(1500)
            except (RuntimeError, AttributeError):
                pass

        self.main_ui._anim_prefetch_worker = None
        self.main_ui._anim_prefetch_thread = None
        self.main_ui._anim_drop_autoplay = False
        self._set_anim_loading(False)

        if hasattr(self.main_ui, "anim_progress"):
            self.main_ui.anim_progress.setVisible(False)
        if hasattr(self.main_ui, "anim_target_progress"):
            self.main_ui.anim_target_progress.setVisible(False)
        if hasattr(self.main_ui, "anim_japan_progress"):
            self.main_ui.anim_japan_progress.setVisible(False)

    def _set_anim_loading(self, loading: bool):
        """Enable/disable the 'Load / Prefetch Sequence' button while a prefetch
        is running so a double-click cannot spawn a second worker thread."""
        btn = getattr(self.main_ui, 'anim_load_btn', None)
        if btn is None:
            return
        try:
            btn.setEnabled(not loading)
        except RuntimeError:
            pass

    def _clear_anim_cache(self):
        self._cancel_anim_prefetch()

        self.main_ui._anim_frames = []
        self.main_ui._anim_timestamps = []
        self.main_ui._anim_nc_paths = []
        self.main_ui._anim_restored_count = 0
        self.main_ui._anim_content_signature = None
        self.main_ui._anim_base_signature = None
        self.main_ui._anim_index = 0
        self.main_ui._anim_playing = False
        self.main_ui._last_shown_valid = None
        self.main_ui._anim_target_gt_cache = {}
        self.main_ui._anim_composite_cache = {}
        self.main_ui._anim_composite_order = []
        self.main_ui._anim_sector_tile_cache = {}
        self.main_ui._anim_exporting = False
        self.main_ui._anim_last_track_cx = None
        self.main_ui._anim_last_track_cy = None
        self.main_ui._anim_geo_mode = False
        self.main_ui._anim_target_frames = []
        self.main_ui._anim_target_crs = []
        self.main_ui._anim_target_gt = []
        self.main_ui._anim_target_nc = []
        self.main_ui._anim_target_item_map = {}
        self.main_ui._anim_target_base = None
        self.main_ui._anim_fldk_item_to_masters = None

        if hasattr(self.main_ui, "_anim_timer"):
            self.main_ui._anim_timer.stop()

        if hasattr(self.main_ui, "anim_frame_slider"):
            self.main_ui.anim_frame_slider.setRange(0, 0)
        if hasattr(self.main_ui, "anim_frame_label"):
            self.main_ui.anim_frame_label.setText("Frame: 0 / 0")
        if hasattr(self.main_ui, "anim_ready_label"):
            self.main_ui.anim_ready_label.setText("Ready: 0 / 0 frames")
        if hasattr(self.main_ui, "anim_play_btn"):
            self.main_ui.anim_play_btn.setText("\u25B6 Play")

        if hasattr(self.main_ui, 'cache'):
            self.main_ui.cache.clear_raw()
            self.main_ui.cache.clear_rgb()
            self.main_ui.cache.clear_precached()
        if hasattr(self.main_ui, '_projected_coast_cache'):
            self.main_ui._projected_coast_cache.clear()
        if hasattr(self.main_ui, '_projected_grid_cache'):
            self.main_ui._projected_grid_cache.clear()

        self.main_ui._overlay_transformer = None
        self.main_ui._overlay_crs = None

        # Frame-registration state is derived per sequence; drop it so the next
        # load rebuilds shifts/refs from scratch instead of mixing stale data.
        self.main_ui._anim_frame_shifts = []
        self.main_ui._anim_target_shifts = []
        self.main_ui._anim_frame_deltas = []
        self.main_ui._anim_target_deltas = []
        self.main_ui._anim_frame_native_dims = []
        self.main_ui._anim_target_native_dims = []
        self.main_ui._anim_ref_gt = None
        self.main_ui._anim_ref_crs = None
        self.main_ui._anim_ref_sub = None
        self.main_ui._anim_target_ref_gt = {}
        self.main_ui._anim_target_ref_sub = {}
        self._composite_cache_bytes = 0

        self.main_ui.log("Animation sequence cache + supporting caches cleared. Next 'Load / Prefetch Sequence' will fully reload and re-process the data.")

    def _find_nearest_valid_frame(self, idx: int) -> int:
        frames = getattr(self.main_ui, "_anim_frames", [])
        if not frames or not (0 <= idx < len(frames)):
            return -1
        if frames[idx] is not None:
            return idx

        n = len(frames)
        for dist in range(1, n):
            for sign in (-1, 1):
                j = idx + sign * dist
                if 0 <= j < n and frames[j] is not None:
                    return j
        return -1

    def _display_anim_frame(self, idx: int):
        frames = getattr(self.main_ui, "_anim_frames", [])
        if not frames or not (0 <= idx < len(frames)):
            return

        arr = frames[idx]
        display_idx = idx

        if arr is None:
            # Lazy render from cached band arrays when a frame is not yet shown
            try:
                band_frames = getattr(self.main_ui, '_anim_band_frames', [])
                ctype, csel = self.main_ui._get_anim_active_content()
                if band_frames and idx < len(band_frames) and band_frames[idx] and csel:
                    bc = band_frames[idx]
                    sat = self.main_ui.anim_sat_combo.currentText() if hasattr(self.main_ui, 'anim_sat_combo') else "himawari9"
                    eng = get_engine(sat)
                    raw = bc.get(csel)
                    if raw is not None:
                        gray = eng._linear(raw, None, None, gamma=1.0)
                        alpha = eng._earth_mask([raw])
                        arr = np.stack([gray, gray, gray, alpha], axis=-1)
                    if arr is not None:
                        _dy, _dx = self._get_anim_frame_shift(idx)
                        arr = self._shift_frame_arr(arr, _dy, _dx)
                        frames[idx] = arr
            except Exception:
                arr = None

        if arr is None:
            nearest = self._find_nearest_valid_frame(idx)
            if nearest == -1:
                self.main_ui.log(f"Frame {idx+1} data not ready yet. (No frames have data in this sequence)")
                return
            if nearest != idx:
                display_idx = nearest
                arr = frames[display_idx]

                if hasattr(self.main_ui, "anim_frame_slider"):
                    self.main_ui.anim_frame_slider.blockSignals(True)
                    self.main_ui.anim_frame_slider.setValue(display_idx)
                    self.main_ui.anim_frame_slider.blockSignals(False)
                self.main_ui._anim_index = display_idx

                if getattr(self.main_ui, "_last_shown_valid", None) != display_idx:
                    self.main_ui.log(f"Frame {idx+1} has no data -- showing nearest available (frame {display_idx+1})")
                    self.main_ui._last_shown_valid = display_idx
            else:
                return

        try:
            if arr.dtype != np.uint8:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
            if arr.ndim == 2:
                arr = np.stack([arr, arr, arr], axis=-1)
            if arr.shape[2] == 3:
                h, w = arr.shape[:2]
                arr = np.concatenate([arr, np.full((h, w, 1), 255, dtype=np.uint8)], axis=2)
            if arr.shape[2] != 4:
                self.main_ui.log("Bad frame shape for display.")
                return

            # Reuse a previously composited frame for this slot when the
            # content/style/product signature is unchanged.
            _sig = self._anim_composite_sig(display_idx)
            _cc = getattr(self.main_ui, '_anim_composite_cache', None) or {}
            _hit = _cc.get(display_idx)
            _use_cached = False
            if _hit is not None and _hit[0] == _sig and _hit[1] is not None:
                arr = _hit[1]
                _use_cached = True

            if not _use_cached and getattr(self.main_ui, '_anim_geo_mode', False) and getattr(self.main_ui, '_anim_target_frames', []):
                _geo_text = self.main_ui.anim_type_combo.currentText() if hasattr(self.main_ui, 'anim_type_combo') else ""
                if self._is_geo_target_text(_geo_text) and display_idx < len(self.main_ui._anim_target_frames):
                    merged = self._composite_geotarget(arr, display_idx)
                    if merged is not None:
                        arr = merged

            # Attempt to display Scene product from cached band data if possible
            prod = getattr(self.main_ui, 'selected_product', None)
            self.main_ui._anim_sync_target_meta = None
            if not _use_cached and prod is not None:
                band_frames = getattr(self.main_ui, '_anim_band_frames', None)
                if band_frames is not None and display_idx < len(band_frames):
                    band_cache = band_frames[display_idx]
                    if band_cache is not None:
                        # Build product from band cache synchronously
                        arr_prod = None
                        eng = None
                        try:
                            prod_info = get_products(self.main_ui.sat_combo.currentText()).get(prod)
                        except Exception:
                            prod_info = None
                        if prod_info is not None and prod_info.get("color_scale"):
                            # Professional / color-scale product: render from cached bands
                            arr_prod = self._render_color_scale_from_cache(prod, prod_info, band_cache)
                        else:
                            required = self.main_ui.satellite_controller._required_bands_for_key(prod)
                            if all(b in band_cache for b in required):
                                nc_path = self._resolve_display_nc(display_idx)
                                try:
                                    # Use the same engine as satellite controller
                                    sat = self.main_ui.anim_sat_combo.currentText() if hasattr(self.main_ui, 'anim_sat_combo') else "himawari9"
                                    eng = get_engine(sat)
                                    arr_prod = eng.composite_realtime(
                                        prod, nc_path, self.main_ui.preview_max_px,
                                        band_cache=band_cache, band_file_map=None
                                    )
                                except Exception:
                                    arr_prod = None
                        if arr_prod is not None:
                            # Co-register the product re-render to the same
                            # ground reference as the pre-built frames.
                            _pdy, _pdx = self._get_anim_frame_shift(display_idx)
                            if _pdy or _pdx:
                                arr_prod = self._shift_frame_arr(arr_prod, _pdy, _pdx)
                            # Paste the target-area PRODUCT (built from per-sector
                            # target band caches) so the target insets follow the
                            # selected Scene product just like the full disk. Fall
                            # back to the raw band frames when the product cannot
                            # be built for a sector (missing checked bands).
                            _anim_paste_meta = None
                            if getattr(self.main_ui, '_anim_geo_mode', False) and getattr(self.main_ui, '_anim_target_frames', []):
                                _geo_text = self.main_ui.anim_type_combo.currentText() if hasattr(self.main_ui, 'anim_type_combo') else ""
                                if self._is_geo_target_text(_geo_text) and display_idx < len(self.main_ui._anim_target_frames):
                                    try:
                                        tbf_m = getattr(self.main_ui, '_anim_target_band_frames', None) or []
                                        gt_list_m = getattr(self.main_ui, '_anim_geotransform_list', None) or []
                                        crs_list_m = getattr(self.main_ui, '_anim_crs_list', None) or []
                                        tgt_gt_m = getattr(self.main_ui, '_anim_target_gt', None) or []
                                        tgt_crs_m = getattr(self.main_ui, '_anim_target_crs', None) or []
                                        tgt_nc_m = getattr(self.main_ui, '_anim_target_nc', None) or []
                                        gt_f_m = getattr(self.main_ui, '_anim_ref_gt', None) or (gt_list_m[display_idx] if 0 <= display_idx < len(gt_list_m) else None)
                                        crs_f_m = getattr(self.main_ui, '_anim_ref_crs', None) or (crs_list_m[display_idx] if 0 <= display_idx < len(crs_list_m) else None)
                                        tg_m = tgt_gt_m[display_idx] if 0 <= display_idx < len(tgt_gt_m) else {}
                                        tc_m = tgt_crs_m[display_idx] if 0 <= display_idx < len(tgt_crs_m) else {}
                                        tn_m = tgt_nc_m[display_idx] if 0 <= display_idx < len(tgt_nc_m) else []
                                        nc_by_sector_m = {_s: (_n, _bfm) for _s, _n, _bfm in tn_m}
                                        targets_m = []
                                        if 0 <= display_idx < len(tbf_m) and tbf_m[display_idx]:
                                            for sector, bc in tbf_m[display_idx].items():
                                                if not bc:
                                                    continue
                                                _tnc_m, _tbm_m = nc_by_sector_m.get(sector, (None, None))
                                                targets_m.append((sector, dict(bc),
                                                                  tg_m.get(sector), tc_m.get(sector),
                                                                  _tnc_m, _tbm_m))
                                        _anim_paste_meta = {'gt_f': gt_f_m, 'crs_f': crs_f_m, 'targets': targets_m}
                                    except Exception:
                                        _anim_paste_meta = None
                                    if _anim_paste_meta and targets_m:
                                        arr_prod = self._composite_product_geotarget(
                                            arr_prod, prod, _anim_paste_meta,
                                            fallback_frames=self.main_ui._anim_target_frames[display_idx],
                                            engine_cls=eng)
                                    else:
                                        arr_prod = self._composite_geotarget(arr_prod, display_idx)
                            # Record sync meta so Scene consumers/export see the same paste
                            if _anim_paste_meta:
                                self.main_ui._anim_sync_target_meta = _anim_paste_meta
                            # Convert product array to QImage and display
                            if arr_prod.dtype != np.uint8:
                                arr_prod = np.clip(arr_prod, 0, 255).astype(np.uint8)
                            if arr_prod.ndim == 2:
                                arr_prod = np.stack([arr_prod, arr_prod, arr_prod], axis=-1)
                            if arr_prod.shape[2] == 3:
                                h, w = arr_prod.shape[:2]
                                arr_prod = np.concatenate([arr_prod, np.full((h, w, 1), 255, dtype=np.uint8)], axis=2)
                            if arr_prod.shape[2] == 4:
                                # Cache the composited product frame for this slot
                                self._store_composite(display_idx, _sig, arr_prod)
                                qimg = QImage(arr_prod.tobytes(), arr_prod.shape[1], arr_prod.shape[0],
                                              arr_prod.shape[1] * 4, QImage.Format_RGBA8888)
                                pix = QPixmap.fromImage(qimg)
                                # Apply same resizing and scene pos as for band frame
                                _saved_crs = self.main_ui.current_crs
                                _saved_gt = self.main_ui.current_geotransform
                                _idx = display_idx
                                _ref_crs = getattr(self.main_ui, '_anim_ref_crs', None)
                                _ref_gt = getattr(self.main_ui, '_anim_ref_gt', None)
                                if _ref_crs is not None and _ref_gt is not None:
                                    self.main_ui.current_crs = _ref_crs
                                    self.main_ui.current_geotransform = _ref_gt
                                elif _idx < len(self.main_ui._anim_crs_list) and self.main_ui._anim_crs_list[_idx] is not None:
                                    self.main_ui.current_crs = self.main_ui._anim_crs_list[_idx]
                                    self.main_ui.current_geotransform = self.main_ui._anim_geotransform_list[_idx]
                                _anim_sector = self.main_ui.anim_type_combo.currentText() if hasattr(self.main_ui, 'anim_type_combo') else None
                                _frame_gt = self.main_ui.current_geotransform
                                pix = self.main_ui._resize_for_fldk(pix, sector=_anim_sector, use_ref_gt=(_ref_gt is not None))
                                scene_pos = self.main_ui._get_image_scene_pos()
                                self.main_ui._anim_export_pixmap = pix
                                self.main_ui.current_crs = _saved_crs
                                self.main_ui.current_geotransform = _saved_gt

                                _mv_owns_anim = False
                                if self.main_ui._mv_manager:
                                    for _win in self.main_ui._mv_manager.get_windows():
                                        if _win.current_mode() == "animation":
                                            _mv_owns_anim = True
                                            break
                                if not _mv_owns_anim:
                                    _mp_mgr = getattr(self.main_ui, '_mp_manager', None)
                                    if _mp_mgr and _mp_mgr.is_active() and _mp_mgr.panels_in_mode("animation"):
                                        _mv_owns_anim = True
                                if not _mv_owns_anim:
                                    self.main_ui.graphics_view.set_image(pix, preserve_view=True, scene_pos=scene_pos)
                                    if getattr(self.main_ui, '_anim_just_started', False):
                                        self.main_ui._anim_just_started = False
                                        gv = self.main_ui.graphics_view
                                        gv.fit_to_image()
                                        gv.zoom_factor = 0.30
                                        gv.resetTransform()
                                        gv.scale(0.30, 0.30)
                                        gv.center_on_image()
                                        gv._user_has_zoomed = True
                                        gv.zoomChanged.emit(gv.zoom_factor)
                                    elif getattr(self.main_ui, 'anim_track_cb', None) and self.main_ui.anim_track_cb.isChecked():
                                        # Same raster->displayed-canvas mapping as the band path,
                                        # but resolved against the composited product canvas.
                                        _afx = pix.width() / max(1, arr_prod.shape[1])
                                        _afy = pix.height() / max(1, arr_prod.shape[0])
                                        _spx = scene_pos.x() if scene_pos is not None else 0.0
                                        _spy = scene_pos.y() if scene_pos is not None else 0.0
                                        _r = self._resolve_track_center(arr_prod, display_idx, _frame_gt)
                                        if _r == ("full",):
                                            self.main_ui.graphics_view.center_on_image()
                                        elif isinstance(_r, tuple) and len(_r) == 2 and _r[0] is not None:
                                            self.main_ui.graphics_view.centerOn(QPointF(_spx + float(_r[0]) * _afx,
                                                                                        _spy + float(_r[1]) * _afy))
                                        elif getattr(self.main_ui, '_anim_geo_mode', False) and getattr(self.main_ui, '_anim_last_track_cx', None) is not None:
                                            self.main_ui.graphics_view.centerOn(
                                                QPointF(_spx + self.main_ui._anim_last_track_cx * _afx,
                                                        _spy + self.main_ui._anim_last_track_cy * _afy))
                                        else:
                                            self.main_ui.graphics_view.center_on_image()
                                # Update frame label and slider
                                ts = self.main_ui._anim_timestamps[display_idx] if display_idx < len(self.main_ui._anim_timestamps) else ""
                                if hasattr(self.main_ui, "anim_frame_label"):
                                    self.main_ui.anim_frame_label.setText(f"Frame: {display_idx+1} / {len(frames)}  |  {ts}")
                                if hasattr(self.main_ui, "anim_frame_slider"):
                                    self.main_ui.anim_frame_slider.blockSignals(True)
                                    self.main_ui.anim_frame_slider.setValue(display_idx)
                                    self.main_ui.anim_frame_slider.blockSignals(False)
                                self.main_ui._anim_index = display_idx
                                self._sync_scene_datetime_to_anim(display_idx)
                                self.main_ui._push_animation_to_multi_viewport(pix)
                                self.main_ui._sync_viewport_anim_bar()
                                self.frame_changed.emit(display_idx)
                                # Skip the rest of the function (including _sync_scene_product_to_anim)
                                return
            if not _use_cached:
                self._store_composite(display_idx, _sig, arr)
            qimg = QImage(arr.tobytes(), arr.shape[1], arr.shape[0],
                          arr.shape[1] * 4, QImage.Format_RGBA8888)
            pix = QPixmap.fromImage(qimg)
            _saved_crs = self.main_ui.current_crs
            _saved_gt = self.main_ui.current_geotransform
            _idx = display_idx
            _ref_crs = getattr(self.main_ui, '_anim_ref_crs', None)
            _ref_gt = getattr(self.main_ui, '_anim_ref_gt', None)
            if _ref_crs is not None and _ref_gt is not None:
                self.main_ui.current_crs = _ref_crs
                self.main_ui.current_geotransform = _ref_gt
            elif _idx < len(self.main_ui._anim_crs_list) and self.main_ui._anim_crs_list[_idx] is not None:
                self.main_ui.current_crs = self.main_ui._anim_crs_list[_idx]
                self.main_ui.current_geotransform = self.main_ui._anim_geotransform_list[_idx]
            _anim_sector = self.main_ui.anim_type_combo.currentText() if hasattr(self.main_ui, 'anim_type_combo') else None
            _frame_gt = self.main_ui.current_geotransform
            pix = self.main_ui._resize_for_fldk(pix, sector=_anim_sector, use_ref_gt=(_ref_gt is not None))
            scene_pos = self.main_ui._get_image_scene_pos()
            self.main_ui.current_crs = _saved_crs
            self.main_ui.current_geotransform = _saved_gt

            _mv_owns_anim = False
            if self.main_ui._mv_manager:
                for _win in self.main_ui._mv_manager.get_windows():
                    if _win.current_mode() == "animation":
                        _mv_owns_anim = True
                        break
            if not _mv_owns_anim:
                _mp_mgr = getattr(self.main_ui, '_mp_manager', None)
                if _mp_mgr and _mp_mgr.is_active() and _mp_mgr.panels_in_mode("animation"):
                    _mv_owns_anim = True
            self.main_ui._anim_export_pixmap = pix
            if not _mv_owns_anim:
                self.main_ui.graphics_view.set_image(pix, preserve_view=True, scene_pos=scene_pos)
                if getattr(self.main_ui, '_anim_just_started', False):
                    self.main_ui._anim_just_started = False
                    gv = self.main_ui.graphics_view
                    gv.fit_to_image()
                    gv.zoom_factor = 0.30
                    gv.resetTransform()
                    gv.scale(0.30, 0.30)
                    gv.center_on_image()
                    gv._user_has_zoomed = True
                    gv.zoomChanged.emit(gv.zoom_factor)
                elif getattr(self.main_ui, 'anim_track_cb', None) and self.main_ui.anim_track_cb.isChecked():
                    _afx = pix.width() / max(1, arr.shape[1])
                    _afy = pix.height() / max(1, arr.shape[0])
                    _spx = scene_pos.x() if scene_pos is not None else 0.0
                    _spy = scene_pos.y() if scene_pos is not None else 0.0
                    _r = self._resolve_track_center(arr, display_idx, _frame_gt)
                    if _r == ("full",):
                        self.main_ui.graphics_view.center_on_image()
                    elif isinstance(_r, tuple) and len(_r) == 2 and _r[0] is not None:
                        self.main_ui.graphics_view.centerOn(QPointF(_spx + float(_r[0]) * _afx,
                                                                    _spy + float(_r[1]) * _afy))
                    elif getattr(self.main_ui, '_anim_geo_mode', False) and getattr(self.main_ui, '_anim_last_track_cx', None) is not None:
                        self.main_ui.graphics_view.centerOn(
                            QPointF(_spx + self.main_ui._anim_last_track_cx * _afx,
                                    _spy + self.main_ui._anim_last_track_cy * _afy))
                    else:
                        self.main_ui.graphics_view.center_on_image()
            self.main_ui._push_animation_to_multi_viewport(pix)
            ts = self.main_ui._anim_timestamps[display_idx] if display_idx < len(self.main_ui._anim_timestamps) else ""
            if hasattr(self.main_ui, "anim_frame_label"):
                self.main_ui.anim_frame_label.setText(f"Frame: {display_idx+1} / {len(frames)}  |  {ts}")
            if hasattr(self.main_ui, "anim_frame_slider"):
                self.main_ui.anim_frame_slider.blockSignals(True)
                self.main_ui.anim_frame_slider.setValue(display_idx)
                self.main_ui.anim_frame_slider.blockSignals(False)
            self.main_ui._anim_index = display_idx
            self._sync_scene_datetime_to_anim(display_idx)
            self._sync_scene_product_to_anim(display_idx)
            self.main_ui._sync_viewport_anim_bar()
            self.frame_changed.emit(display_idx)
        except Exception as e:
            self.main_ui.log(f"Display anim frame {display_idx} error: {e}")
            return None

    def _render_color_scale_from_cache(self, key, info, band_cache):
        try:
            cs = info.get("color_scale", {}) or {}
            cmap_name = cs.get("colormap", "jet")
            use_bt = cs.get("mode", "rad") == "bt"
            gamma = cs.get("gamma", 1.0)
            vmin = cs.get("vmin")
            vmax = cs.get("vmax")
            forced = "B13" if cmap_name in ("BT Enhanced (IR)", "Sandwich (IR)", "Sandwich IR (SATAID)", "Sandwich (SATAID)", "Dvorak (IR)", "Dvorak Experimental", "SST (IR)") else None
            band = forced
            if band is None:
                try:
                    ctype, csel = self.main_ui._get_anim_active_content()
                except Exception:
                    ctype, csel = "BAND", ""
                if ctype == "BAND" and csel in band_cache:
                    band = csel
            if band is None or band not in band_cache:
                for _b in band_cache:
                    band = _b
                    break
            data = band_cache.get(band)
            if data is None:
                self.main_ui.log(f"[ColorScale] '{key}': no usable band in cache ({cmap_name}, forced={forced}, cache={list(band_cache.keys())})")
                return None
            if use_bt:
                try:
                    arr = np.asarray(self.main_ui._radiance_to_bt_cached(data, band), dtype=np.float32)
                except Exception as e:
                    self.main_ui.log(f"[ColorScale] '{key}': BT conversion failed ({type(e).__name__}: {e}) -- using raw values")
                    arr = np.asarray(data, dtype=np.float32)
            else:
                arr = np.asarray(data, dtype=np.float32)
            valid = arr[np.isfinite(arr)]
            if len(valid) == 0:
                self.main_ui.log(f"[ColorScale] '{key}': band {band} has no finite values")
                return None
            if vmin is None or vmax is None:
                lo, hi = float(valid.min()), float(valid.max())
            else:
                lo, hi = float(vmin), float(vmax)
            if hi <= lo:
                hi = lo + 1e-6
            norm = np.clip((arr - lo) / (hi - lo), 0.0, 1.0)
            if gamma != 1.0 and gamma > 0:
                norm = np.power(norm, 1.0 / gamma)
            lut = self.main_ui._cmap_lut(cmap_name)
            if lut is None:
                self.main_ui.log(f"[ColorScale] '{key}': no LUT for cmap '{cmap_name}'")
                return None
            norm_clean = np.nan_to_num(norm, nan=0.0)
            idx = np.clip(np.floor(norm_clean * (lut.shape[0] - 1)).astype(np.int32), 0, lut.shape[0] - 1)
            rgb_u8 = lut[idx]
            alpha = np.where(np.isnan(arr), 0, 255).astype(np.uint8)
            h, w = rgb_u8.shape[:2]
            rgba = np.concatenate([rgb_u8, alpha[:, :, np.newaxis]], axis=-1)
            return rgba
        except Exception as e:
            try:
                import traceback
                self.main_ui.log(f"[ColorScale] '{key}' failed ({type(e).__name__}: {e})")
                self.main_ui.log(traceback.format_exc())
            except Exception:
                pass
            return None

    def _resolve_display_nc(self, display_idx):
        ui = self.main_ui
        paths = getattr(ui, '_anim_nc_paths', None)
        if not paths:
            return None
        for j in range(display_idx, -1, -1):
            entry = paths[j]
            nc_path = entry[0] if isinstance(entry, tuple) and entry else entry
            if isinstance(nc_path, (list, tuple)):
                nc_path = nc_path[0] if nc_path else None
            if nc_path:
                return nc_path
        return None

    def _sync_scene_product_to_anim(self, display_idx):
        ui = self.main_ui
        if getattr(ui, '_anim_playing', False) or getattr(ui, '_anim_exporting', False):
            return
        prod = getattr(ui, 'selected_product', None)
        if not prod:
            return
        if getattr(ui, 'generating_product', False):
            ui._anim_sync_pending_idx = display_idx
            self._scene_prod_sync_sig = None
            return
        nc_path = self._resolve_display_nc(display_idx)
        if not nc_path:
            return
        tbf = getattr(ui, '_anim_target_band_frames', None) or []
        has_target = (0 <= display_idx < len(tbf)) and bool(tbf[display_idx])
        tgt_sig = tuple(sorted((s, id(bc)) for s, bc in (tbf[display_idx].items() if has_target else [])))
        sync_sig = (display_idx, tgt_sig, prod)
        if getattr(self, '_scene_prod_sync_sig', None) == sync_sig:
            return
        frames = getattr(ui, '_anim_band_frames', None) or []
        if not (0 <= display_idx < len(frames)) or not frames[display_idx]:
            return
        if not has_target:
            try:
                cur = getattr(ui, 'current_original', None)
                if cur and Path(cur).resolve() == Path(nc_path).resolve():
                    self._scene_prod_sync_sig = sync_sig
                    self._scene_prod_sync_prod = prod
                    return
            except Exception:
                pass
        import time as _t
        now = _t.perf_counter()
        changed_prod = getattr(self, '_scene_prod_sync_prod', None) != prod
        if not changed_prod and now - getattr(self, '_scene_prod_sync_at', 0.0) < 0.35:
            return
        self._scene_prod_sync_at = now
        ui._anim_sync_product_idx = display_idx
        if has_target:
            gt_list = getattr(ui, '_anim_geotransform_list', None) or []
            crs_list = getattr(ui, '_anim_crs_list', None) or []
            tgt_gt = getattr(ui, '_anim_target_gt', None) or []
            tgt_crs = getattr(ui, '_anim_target_crs', None) or []
            tgt_nc = getattr(ui, '_anim_target_nc', None) or []
            gt_f = gt_list[display_idx] if 0 <= display_idx < len(gt_list) else None
            crs_f = crs_list[display_idx] if 0 <= display_idx < len(crs_list) else None
            tg = tgt_gt[display_idx] if 0 <= display_idx < len(tgt_gt) else {}
            tc = tgt_crs[display_idx] if 0 <= display_idx < len(tgt_crs) else {}
            tn = tgt_nc[display_idx] if 0 <= display_idx < len(tgt_nc) else []
            nc_by_sector = {_s: (_n, _bfm) for _s, _n, _bfm in tn}
            targets = []
            for sector, bc in tbf[display_idx].items():
                if not bc:
                    continue
                _tnc, _tbm = nc_by_sector.get(sector, (None, None))
                targets.append((sector, dict(bc), tg.get(sector), tc.get(sector), _tnc, _tbm))
            ui._anim_sync_target_meta = {'gt_f': gt_f, 'crs_f': crs_f, 'targets': targets}
        else:
            ui._anim_sync_target_meta = None
        try:
            ui.current_nc_path = nc_path
            ui.satellite_controller.generate_rgb_product(prod, _from_animation=True)
        except Exception as e:
            ui.log('Anim->scene product sync error: ' + str(e))
        self._scene_prod_sync_sig = sync_sig
        self._scene_prod_sync_prod = prod

    def _sync_scene_datetime_to_anim(self, display_idx):
        ui = self.main_ui
        timestamps = getattr(ui, '_anim_timestamps', [])
        if not (0 <= display_idx < len(timestamps)):
            return
        ts = timestamps[display_idx]
        if not ts:
            return
        try:
            parts = ts.split('_')
            y = parts[0]
            mo = parts[1] if len(parts) > 1 else ''
            d = parts[2] if len(parts) > 2 else ''
            hm = parts[3] if len(parts) > 3 else ''
            h = hm[:2]
            mi = hm[2:4] if len(hm) >= 4 else '00'
            for combo, val in ((getattr(ui, 'year_combo', None), y),
                               (getattr(ui, 'month_combo', None), mo),
                               (getattr(ui, 'day_combo', None), d),
                               (getattr(ui, 'hour_combo', None), h),
                               (getattr(ui, 'minute_combo', None), mi)):
                if combo is None or not val:
                    continue
                try:
                    combo.blockSignals(True)
                    if combo.findText(val) == -1:
                        combo.addItem(val)
                    combo.setCurrentText(val)
                except Exception:
                    pass
                combo.blockSignals(False)
            ui.current_datetime = ts
        except Exception:
            pass

    def _resize_rgba(self, arr, out_h, out_w):
        from scipy.ndimage import zoom
        h, w = arr.shape[:2]
        if (h, w) == (out_h, out_w):
            return arr.astype(np.uint8, copy=False)
        if h == 0 or w == 0:
            return arr
        zh = out_h / h
        zw = out_w / w
        out = np.empty((out_h, out_w, arr.shape[2]), dtype=np.float32)
        for c in range(arr.shape[2]):
            out[:, :, c] = zoom(arr[:, :, c].astype(np.float32), (zh, zw), order=1)
        return np.clip(out, 0, 255).astype(np.uint8)


    def _resolve_target_gt(self, base_arr, gt_f, gt_t, tarr, scl_x, scl_y, sector, thr, tw):
        try:
            cache = self.main_ui._anim_target_gt_cache
        except AttributeError:
            cache = {}
            self.main_ui._anim_target_gt_cache = cache
        try:
            key = (sector, tuple(round(float(v), 3) for v in gt_t.to_gdal()), thr, tw)
            if key in cache:
                return cache[key]

            if gt_f is None or abs(float(gt_f.a)) < 1e-9 or abs(float(gt_f.e)) < 1e-9:
                cache[key] = gt_t
                return gt_t

            a_f = float(gt_f.a)
            e_f = float(gt_f.e)
            c_f = float(gt_f.c)
            f_f = float(gt_f.f)
            a_t = float(gt_t.a)
            e_t = float(gt_t.e)
            c_t = float(gt_t.c)
            f_t = float(gt_t.f)

            try:
                tg = tarr[:, :, 0].astype(np.float32)
            except Exception:
                cache[key] = gt_t
                return gt_t

            fin = np.nanmean(tg) if np.isfinite(tg).any() else 0.0
            tg = np.where(np.isfinite(tg), tg, fin)
            if float(np.ptp(tg)) < 1e-6:
                cache[key] = gt_t
                return gt_t

            from scipy.ndimage import zoom as _zoom
            S = 64
            tg_s = _zoom(tg, (S / thr, S / tw), order=1)
            hgt, wgt = base_arr.shape[:2]

            def _correlate(mode):
                a2, e2, c2, f2 = a_t, e_t, c_t, f_t
                if mode == 1:
                    f2 = -(f_t + e_t * thr)
                elif mode == 2:
                    c2 = c_t + a_t * tw
                    a2 = -a_t
                elif mode == 3:
                    f2 = -(f_t + e_t * thr)
                    c2 = c_t + a_t * tw
                    a2 = -a_t
                col0 = (c2 - c_f) / a_f * scl_x
                row0 = (f_f - f2) / -e_f * scl_y
                dw = max(8, int(round(tw * abs(a2 / a_f) * scl_x)))
                dh = max(8, int(round(thr * abs(e2 / e_f) * scl_y)))
                l = int(round(col0))
                t = int(round(row0))
                if l >= wgt or t >= hgt or l + dw <= 0 or t + dh <= 0:
                    return -1.0
                sl = max(0, l)
                st = max(0, t)
                el = min(wgt, l + dw)
                et = min(hgt, t + dh)
                if el <= sl or et <= st:
                    return -1.0
                patch = base_arr[st:et, sl:el, 0].astype(np.float32)
                pm = np.nanmean(patch) if np.isfinite(patch).any() else 0.0
                patch = np.where(np.isfinite(patch), patch, pm)
                if float(np.ptp(patch)) < 1e-6:
                    return -1.0
                p_s = _zoom(patch, (S / (et - st), S / (el - sl)), order=1)
                pa = p_s - p_s.mean()
                ta = tg_s - tg_s.mean()
                den = float(np.sqrt(float((pa * pa).sum()) * float((ta * ta).sum())))
                if den > 1e-9:
                    return float((pa * ta).sum()) / den
                return -1.0

            cr0 = _correlate(0)
            best_corr, best_mode = cr0, 0
            for m in (1, 2, 3):
                cm = _correlate(m)
                if cm > best_corr:
                    best_corr, best_mode = cm, m

            if best_mode != 0 and best_corr >= 0.45 and best_corr > cr0 + 0.15:
                a2, e2, c2, f2 = a_t, e_t, c_t, f_t
                if best_mode == 1:
                    f2 = -(f_t + e_t * thr)
                elif best_mode == 2:
                    c2 = c_t + a_t * tw
                    a2 = -a_t
                else:
                    f2 = -(f_t + e_t * thr)
                    c2 = c_t + a_t * tw
                    a2 = -a_t
                from rasterio.transform import Affine
                out = Affine(a2, gt_t.b, c2, gt_t.d, e2, f2)
                cache[key] = out
                try:
                    self.main_ui.log(f"Geo Target {sector}: corrected geotransform orientation (mode={best_mode}, corr={best_corr:.2f} vs declared {cr0:.2f})")
                except Exception:
                    pass
                return out

            cache[key] = gt_t
            return gt_t
        except Exception:
            return gt_t

    def _read_coff_loff(self, nc_path):
        """Parse JMA HSD Header Block #3 nav values (COFF/LOFF/CFAC/LFAC) from
        the .ads.json sidecar. Returns a dict or None. These describe the
        geostationary scanning angles that geolocate the sub-area natively."""
        try:
            import json as _json
            cache = getattr(self, '_coff_loff_cache', None)
            if cache is None:
                cache = self._coff_loff_cache = {}
            key = str(nc_path)
            if key in cache:
                return cache[key]
            out = None
            side = self._find_target_sidecar(nc_path)
            if side is not None and side.exists():
                with open(side, 'r', encoding='utf-8') as _fh:
                    d = _json.load(_fh)
                cl = d.get('coff_loff') or {}
                coff = cl.get('COFF')
                loff = cl.get('LOFF')
                cfac = cl.get('CFAC')
                lfac = cl.get('LFAC')
                try:
                    coff = float(coff); loff = float(loff)
                    cfac = float(cfac); lfac = float(lfac)
                except (TypeError, ValueError):
                    coff = loff = cfac = lfac = None
                if coff is not None and loff is not None and cfac and lfac and cfac > 0 and lfac > 0:
                    out = {'COFF': coff, 'LOFF': loff, 'CFAC': cfac, 'LFAC': lfac}
            cache[key] = out
            return out
        except Exception:
            return None

    @staticmethod
    def _shift_frame_arr(arr, dy, dx):
        """Translate a gray/RGB/RGBA frame by ``(dy, dx)`` pixels with zero-fill.

        Positive ``dy``/``dx`` move content down/right. This is used to
        co-register every animation frame so each scan's ground subpoint lands
        on the same reference pixel, which removes the slow frame-to-frame
        drift between consecutive geostationary scans.
        """
        if arr is None:
            return arr
        try:
            dy = int(dy)
            dx = int(dx)
            if not (dy or dx):
                return arr
            h, w = arr.shape[:2]
            if h < 1 or w < 1:
                return arr
            dy = max(-h, min(h - 1, dy))
            dx = max(-w, min(w - 1, dx))
            if not (dy or dx):
                return arr
            src_r0 = max(0, -dy)
            src_r1 = min(h, h - dy)
            src_c0 = max(0, -dx)
            src_c1 = min(w, w - dx)
            if src_r1 <= src_r0 or src_c1 <= src_c0:
                return arr
            out = np.zeros_like(arr)
            out[src_r0 + dy:src_r1 + dy, src_c0 + dx:src_c1 + dx] = arr[src_r0:src_r1, src_c0:src_c1]
            return out
        except Exception:
            return arr

    @staticmethod
    def _subpoint_from_gt(gt):
        """Native grid ``(row, col)`` of the projection origin for a geotransform."""
        try:
            if gt is None or abs(float(gt.a)) < 1e-9 or abs(float(gt.e)) < 1e-9:
                return None
            return (float(-gt.f / gt.e), float(-gt.c / gt.a))
        except Exception:
            return None

    @staticmethod
    def _gt_native_dims(gt):
        """``(h, w)`` native pixel size implied by a geotransform, or None."""
        try:
            if gt is None or abs(float(gt.a)) < 1e-9 or abs(float(gt.e)) < 1e-9:
                return None
            nw = max(1, int(round(2 * abs(gt.c) / abs(gt.a))))
            nh = max(1, int(round(2 * abs(gt.f) / abs(gt.e))))
            return (nh, nw)
        except Exception:
            return None

    def _nc_subpoint(self, nc_path, gt):
        """Native ``(row, col)`` of a scan's ground subpoint.

        Uses the HSD COFF/LOFF nav values when available (exact, per scan),
        otherwise falls back to the subpoint implied by the geotransform.
        """
        nav = self._read_coff_loff(nc_path) if nc_path is not None else None
        if nav is not None:
            try:
                return (float(nav['LOFF']), float(nav['COFF']))
            except (KeyError, TypeError, ValueError):
                pass
        return self._subpoint_from_gt(gt)

    def _setup_anim_registration(self):
        """Pick a single ground reference for the whole sequence and record the
        native-pixel offset of every frame (base + target sectors) relative to it.

        All frames are later shifted onto this reference at frame-ready time and
        every display/placement computation is anchored to the reference
        geotransform, so nothing drifts between consecutive scans.
        """
        ui = self.main_ui
        n = len(getattr(ui, '_anim_nc_paths', None) or [])
        ui._anim_frame_shifts = [(0, 0)] * n
        ui._anim_target_shifts = [{} for _ in range(n)]
        ui._anim_frame_deltas = [(0.0, 0.0)] * n
        ui._anim_frame_native_dims = [None] * n
        ui._anim_target_deltas = [{} for _ in range(n)]
        ui._anim_target_native_dims = [{} for _ in range(n)]
        ui._anim_ref_gt = None
        ui._anim_ref_crs = None
        ui._anim_ref_sub = None
        ui._anim_target_ref_gt = {}
        ui._anim_target_ref_sub = {}
        if n == 0:
            return

        # Base (full-disk / primary) reference: first slot that owns NC data.
        base_idx = -1
        for i in range(n):
            if ui._anim_nc_paths[i] is not None:
                base_idx = i
                break
        if base_idx >= 0:
            entry = ui._anim_nc_paths[base_idx]
            nc = entry[0] if isinstance(entry, tuple) else entry
            gt = ui._anim_geotransform_list[base_idx] if base_idx < len(ui._anim_geotransform_list) else None
            crs = ui._anim_crs_list[base_idx] if base_idx < len(ui._anim_crs_list) else None
            ui._anim_ref_gt = gt
            ui._anim_ref_crs = crs
            ui._anim_ref_sub = self._nc_subpoint(nc, gt)

        ref_sub = ui._anim_ref_sub
        for i in range(n):
            entry = ui._anim_nc_paths[i]
            if entry is None:
                continue
            nc = entry[0] if isinstance(entry, tuple) else entry
            gt = ui._anim_geotransform_list[i] if i < len(ui._anim_geotransform_list) else None
            sub = self._nc_subpoint(nc, gt)
            if sub is not None and ref_sub is not None:
                ui._anim_frame_deltas[i] = (sub[0] - ref_sub[0], sub[1] - ref_sub[1])
            ui._anim_frame_native_dims[i] = self._gt_native_dims(gt)

        # Carried slots (geo-mode full-disk inheritance) reuse the owner's offset.
        fldk_map = getattr(ui, '_anim_fldk_item_to_masters', None)
        if fldk_map:
            for owner, masters in fldk_map.items():
                for m in masters:
                    if m != owner and 0 <= m < n:
                        ui._anim_frame_deltas[m] = ui._anim_frame_deltas[owner]
                        ui._anim_frame_native_dims[m] = ui._anim_frame_native_dims[owner]

        # Per-sector references + offsets.
        sectors = set()
        for slots in (getattr(ui, '_anim_target_nc', None) or []):
            for _s, _nc, _bfm in (slots or []):
                sectors.add(_s)
        for s in sectors:
            for i in range(n):
                found = None
                for _s, _nc, _bfm in (ui._anim_target_nc[i] or []):
                    if _s == s:
                        found = _nc
                        break
                if found is not None:
                    gt = ui._anim_target_gt[i].get(s) if i < len(ui._anim_target_gt) else None
                    ui._anim_target_ref_gt[s] = gt
                    ui._anim_target_ref_sub[s] = self._nc_subpoint(found, gt)
                    break
        for i in range(n):
            for _s, _nc, _bfm in (ui._anim_target_nc[i] or []):
                gt = ui._anim_target_gt[i].get(_s) if i < len(ui._anim_target_gt) else None
                ref_sub = ui._anim_target_ref_sub.get(_s)
                sub = self._nc_subpoint(_nc, gt)
                if sub is not None and ref_sub is not None:
                    ui._anim_target_deltas[i][_s] = (sub[0] - ref_sub[0], sub[1] - ref_sub[1])
                ui._anim_target_native_dims[i][_s] = self._gt_native_dims(gt)

    def _apply_frame_shift(self, arr, drow, dcol, native_dims):
        """Shift ``arr`` so its ground subpoint lands on the sequence reference.

        Returns ``(shifted_arr, dy, dx)`` where ``dy``/``dx`` are in array pixels.
        """
        if arr is None:
            return arr, 0, 0
        try:
            h, w = arr.shape[:2]
            scaley = scalex = 1.0
            if native_dims:
                nh, nw = native_dims
                if nh and nw:
                    scaley = h / float(nh)
                    scalex = w / float(nw)
            dy = int(round(-float(drow) * scaley))
            dx = int(round(-float(dcol) * scalex))
            arr = self._shift_frame_arr(arr, dy, dx)
            return arr, dy, dx
        except Exception:
            return arr, 0, 0

    def _get_anim_frame_shift(self, idx):
        """Array-pixel ``(dy, dx)`` shift applied to the displayed frame at ``idx``."""
        ui = self.main_ui
        try:
            shifts = getattr(ui, '_anim_frame_shifts', None) or []
            if 0 <= idx < len(shifts):
                return shifts[idx]
        except Exception:
            pass
        return (0, 0)

    def _scan_angle_target_gt(self, nav, ref_gt, thr, tw, declared=None):
        """Build the true sub-area geotransform from JMA scanning angles
        (``_read_coff_loff`` result) anchored onto the verified full-disk
        geotransform ``ref_gt``. ``declared`` (optional) supplies the array's
        own pixel size; ``thr/tw`` are accepted for API parity.
        Returns a rasterio Affine or None."""
        try:
            if nav is None or ref_gt is None:
                return None
            a_f = abs(float(ref_gt.a))
            e_f = -a_f
            if a_f < 1e-9:
                return None
            if declared is not None:
                try:
                    a_d = abs(float(declared.a))
                    if 0.25 * a_f <= a_d <= 4.0 * a_f:
                        a_f = a_d
                        e_f = -a_d
                except Exception:
                    pass
            coff = nav.get('COFF')
            loff = nav.get('LOFF')
            cfac = nav.get('CFAC')
            lfac = nav.get('LFAC')
            if coff is None or loff is None or not cfac or not lfac:
                return None
            # Full-disk native index of the geostationary subpoint.
            sub_x = -float(ref_gt.c) / a_f
            sub_y = -float(ref_gt.f) / e_f
            # Degrees per HSD index (sub-area) and per full-disk index.
            import math as _math
            h_sat = 35785863.0
            pu_t_x = 65536.0 / float(cfac)
            pu_t_y = 65536.0 / float(lfac)
            pu_f = a_f * 180.0 / (_math.pi * h_sat)
            if pu_f < 1e-12:
                return None
            col0 = sub_x + (0.0 - coff) * (pu_t_x / pu_f)
            line0 = sub_y + (0.0 - loff) * (pu_t_y / pu_f)
            from rasterio.transform import Affine
            out = Affine(a_f, 0.0, float(ref_gt.c) + col0 * a_f,
                         0.0, e_f, float(ref_gt.f) + line0 * e_f)
            try:
                ui = getattr(self, 'main_ui', None)
                if ui is not None and hasattr(ui, 'log'):
                    ui.log(f"[DIAG-scan-gt] coff={coff} loff={loff} cfac={cfac} lfac={lfac} "
                           f"sub=(sub_x={sub_x:.1f},sub_y={sub_y:.1f}) pu_t=({pu_t_x:.6g},{pu_t_y:.6g}) "
                           f"pu_f={pu_f:.6g} out={out}")
            except Exception:
                pass
            return out
        except Exception:
            return None

    def _scene_geo_gt(self, nc_path):
        """Full-disk geotransform only (from the nearest FLDK sidecar; no image
        decode) so sub-area placement can be computed from scanning angles."""
        ui = self.main_ui
        cache = getattr(ui, '_scene_fl_dk_gt_cache', None)
        if cache is None:
            cache = ui._scene_fl_dk_gt_cache = {}
        key = str(nc_path)
        if key in cache:
            return cache[key]
        out = None
        try:
            fldk_nc = self._find_fl_dk_nc(nc_path)
            if fldk_nc is not None:
                _proj = getattr(ui, 'projection', None)
                if _proj is not None:
                    _c, out = _proj.extract_crs_from_ads(fldk_nc)
                else:
                    _c, out = ui.extract_crs_from_ads(fldk_nc)
        except Exception:
            out = None
        cache[key] = out
        return out

    def _resolve_track_center(self, arr, display_idx, frame_gt):
        ui = self.main_ui
        try:
            cb = getattr(ui, 'anim_track_target_combo', None)
            target = cb.currentData() if cb is not None else None
            if isinstance(target, str):
                if target == 'FLDK':
                    return ('full',)
                if target in ('Target', 'M1', 'M2'):
                    if not getattr(ui, '_anim_geo_mode', False):
                        _typ = None
                        try:
                            _typ = ui.anim_type_combo.currentData()
                        except Exception:
                            _typ = None
                        if _typ in ('Target', 'Meso'):
                            return ('full',)
                    return self._track_target_area_pixel(arr, display_idx, target, frame_gt)
            elif isinstance(target, (tuple, list)) and target and target[0] == 'storm':
                return self._track_storm_pixel(arr, str(target[1]))
            return (None, None)
        except Exception as e:
            try:
                ui.log('Track resolve error: ' + str(e))
            except Exception:
                pass
            return (None, None)

    def _track_target_area_pixel(self, arr, display_idx, sector, frame_gt):
        ui = self.main_ui
        try:
            gt_f = frame_gt or getattr(ui, '_anim_ref_gt', None)
            if gt_f is None or abs(gt_f.a) < 1e-09 or abs(gt_f.e) < 1e-09:
                return (None, None)
            hgt, wgt = arr.shape[:2]
            native_w = max(1, int(round(2 * abs(gt_f.c) / abs(gt_f.a))))
            native_h = max(1, int(round(2 * abs(gt_f.f) / abs(gt_f.e))))
            scl_x = wgt / native_w
            scl_y = hgt / native_h
            tframes = {}
            tgts = {}
            tcrss = {}
            try:
                tframes = ui._anim_target_frames[display_idx]
                tgts = ui._anim_target_gt[display_idx]
                tcrss = ui._anim_target_crs[display_idx]
            except (IndexError, KeyError, TypeError):
                pass
            tarr = tframes.get(sector)
            gt_t = tgts.get(sector)
            crs_t = tcrss.get(sector)
            _tgt_shape = None
            if gt_t is None:
                ts = ui._anim_timestamps[display_idx] if display_idx < len(getattr(ui, '_anim_timestamps', [])) else ''
                if not ts:
                    return (None, None)
                _tgt_crs, _tgt_gt, _tgt_shape, _tgt_nc = self._get_target_gt_for_ts(ts)
                if _tgt_gt is None:
                    return (None, None)
                crs_t = _tgt_crs
                gt_t = _tgt_gt
                tarr = None
            if abs(gt_t.a) < 1e-09 or abs(gt_t.e) < 1e-09:
                return (None, None)
            if tarr is not None:
                thr, tw = tarr.shape[:2]
                gt_t = self._geotarget_gt(ui, display_idx, sector, tarr, arr,
                                          gt_f, gt_t, scl_x, scl_y, thr, tw)
            else:
                thr, tw = (tuple(_tgt_shape[:2]) if _tgt_shape else None) or (550, 550)
                try:
                    _tbc = ui._anim_target_band_frames[display_idx].get(sector)
                except Exception:
                    _tbc = None
                if _tbc:
                    for _a in _tbc.values():
                        if _a is None or getattr(_a, 'ndim', 0) != 2:
                            continue
                        thr, tw = _a.shape[:2]
                        break
                _key = (sector, tuple(round(float(v), 3) for v in gt_t.to_gdal()), thr, tw)
                _corrected = getattr(ui, '_anim_target_gt_cache', {}).get(_key)
                if _corrected is None:
                    # Non-geo ("single") mode: no target frames are composited, so
                    # locate the sub-area from the HSD scanning angles anchored on
                    # the (full-disk) master geotransform instead of content.
                    _scan = None
                    try:
                        _typ = ui.anim_type_combo.currentData() if hasattr(ui, 'anim_type_combo') else None
                        if _typ in ('FLDK', 'Full') and _tgt_nc:
                            _nav = self._read_coff_loff(_tgt_nc)
                            if _nav is not None:
                                _scan = self._scan_angle_target_gt(_nav, gt_f, thr, tw, gt_t)
                                if _scan is not None:
                                    ui._anim_target_gt_cache[_key] = _scan
                    except Exception:
                        _scan = None
                    if _scan is None:
                        return (None, None)
                    gt_t = _scan
                else:
                    gt_t = _corrected
            col0 = (gt_t.c - gt_f.c) / gt_f.a * scl_x
            row0 = (gt_f.f - gt_t.f) / -gt_f.e * scl_y
            dw = tw * abs(gt_t.a / gt_f.a) * scl_x
            dh = thr * abs(gt_t.e / gt_f.e) * scl_y
            cx = col0 + dw / 2.0
            cy = row0 + dh / 2.0
            ui._anim_last_track_cx = cx
            ui._anim_last_track_cy = cy
            if crs_t is not None:
                ui._anim_track_crs = crs_t
                ui._anim_track_gt = gt_t
            return (cx, cy)
        except Exception as e:
            try:
                ui.log('Track target resolve error: ' + str(e))
            except Exception:
                pass
            return (None, None)

    def _track_storm_pixel(self, arr, atcf_id):
        ui = self.main_ui
        try:
            storms = getattr(ui, 'atcf_storms', None) or []
            s = next((s2 for s2 in storms if str(s2.get('atcf_id')) == str(atcf_id)), None)
            if s is None:
                return (None, None)
            lat = s.get('current_lat')
            lon = s.get('current_lon')
            if lat is None or lon is None:
                return (None, None)
            gt = ui.current_geotransform
            crs_proj = ui.current_crs
            if gt is None or crs_proj is None:
                return (None, None)
            from pyproj import Transformer
            tr = Transformer.from_crs('EPSG:4326', crs_proj, always_xy=True)
            xp, yp = tr.transform(float(lon), float(lat))
            hgt, wgt = arr.shape[:2]
            native_w = max(1, int(round(2 * abs(gt.c) / abs(gt.a))))
            native_h = max(1, int(round(2 * abs(gt.f) / abs(gt.e))))
            scl_x = wgt / native_w if native_w else 1.0
            scl_y = hgt / native_h if native_h else 1.0
            cx = float((xp - gt.c) / gt.a * scl_x)
            cy = float((gt.f - yp) / -gt.e * scl_y)
            if np.isfinite(cx) and np.isfinite(cy):
                ui._anim_last_track_cx = cx
                ui._anim_last_track_cy = cy
                try:
                    ui.log(f'[TRACK] storm {atcf_id}: {float(lat):.2f}N, {float(lon):.2f}E -> ({int(cx)},{int(cy)})')
                except Exception:
                    pass
                return (cx, cy)
            return (None, None)
        except Exception as e:
            try:
                ui.log('Track storm resolve error: ' + str(e))
            except Exception:
                pass
            return (None, None)

    def _geotarget_gt(self, ui, idx, sector, tarr, base_arr, gt_f, gt_t,
                      scl_x, scl_y, thr, tw):
        """Resolve the geotransform used to place a geo-target sub-area frame.
        Prefers the JMA HSD scanning-angle geolocation (exact, no full-disk
        image needed); falls back to content-correlation orientation validation."""
        try:
            _key = (sector, tuple(round(float(v), 3) for v in gt_t.to_gdal()), thr, tw)
            try:
                _cached = ui._anim_target_gt_cache.get(_key)
                if _cached is not None:
                    return _cached
            except Exception:
                pass
            nav = None
            t_nc = None
            try:
                for _sector, _tnc, _tbfm in ui._anim_target_nc[idx]:
                    if _sector == sector:
                        t_nc = _tnc
                        break
            except Exception:
                t_nc = None
            if t_nc is not None:
                nav = self._read_coff_loff(t_nc)
            out = None
            if nav is not None:
                out = self._scan_angle_target_gt(nav, gt_f, thr, tw, gt_t)
                if out is not None:
                    try:
                        ui.log(f"Geo Target {sector}: geolocated via HSD scan angles (COFF/LOFF)")
                    except Exception:
                        pass
            if out is None:
                out = self._resolve_target_gt(base_arr, gt_f, gt_t, tarr,
                                              scl_x, scl_y, sector, thr, tw)
            if out is not None:
                try:
                    _key = (sector, tuple(round(float(v), 3) for v in gt_t.to_gdal()), thr, tw)
                    ui._anim_target_gt_cache[_key] = out
                except Exception:
                    pass
            return out
        except Exception as e:
            try:
                ui.log(f"Geo Target {sector} gt error: {e}")
            except Exception:
                pass
            return gt_t

    def _composite_geotarget(self, base_arr, idx):
        ui = self.main_ui
        try:
            targets = ui._anim_target_frames[idx]
            gt_f = getattr(ui, '_anim_ref_gt', None) or ui._anim_geotransform_list[idx]
            crs_f = getattr(ui, '_anim_ref_crs', None) or ui._anim_crs_list[idx]
            if not targets or gt_f is None or crs_f is None:
                return base_arr
            if abs(gt_f.a) < 1e-09 or abs(gt_f.e) < 1e-09:
                return base_arr
            target_gt = ui._anim_target_gt[idx]
            target_crs = ui._anim_target_crs[idx]
            canvas = np.ascontiguousarray(base_arr).astype(np.uint8, copy=True)
            hgt, wgt = canvas.shape[:2]
            native_w = max(1, int(round(2 * abs(gt_f.c) / abs(gt_f.a))))
            native_h = max(1, int(round(2 * abs(gt_f.f) / abs(gt_f.e))))
            scl_x = wgt / native_w
            scl_y = hgt / native_h
            style = self._get_anim_geo_style()
            try:
                color = ui.settings.get('geotarget_border_color', '#00E5FF')
            except Exception:
                color = '#00E5FF'
            border_rgb = self._hex_to_rgb(color)
            japan_mask = None
            for sector in targets:
                tarr = targets[sector]
                if tarr is None:
                    continue
                gt_t = target_gt.get(sector)
                crs_t = target_crs.get(sector)
                if gt_t is None or crs_t is None:
                    continue
                if abs(gt_t.a) < 1e-09 or abs(gt_t.e) < 1e-09:
                    continue
                thr, tw = tarr.shape[:2]
                gt_t = self._geotarget_gt(ui, idx, sector, tarr, base_arr,
                                          gt_f, gt_t, scl_x, scl_y, thr, tw)
                japan_mask = self._paste_sector_tile(
                    canvas, tarr, gt_f, gt_t, scl_x, scl_y, thr, tw,
                    border_rgb, style, sector, japan_mask)
            if style == 'border' and japan_mask is not None:
                ys, xs = np.where(japan_mask > 0)
                if ys.size:
                    xa_ = int(xs.min()); xb_ = int(xs.max()) + 1
                    ya_ = int(ys.min()); yb_ = int(ys.max()) + 1
                    sub = japan_mask[ya_:yb_, xa_:xb_] > 0
                    segs = self._mask_outline_segments(sub)
                    if segs:
                        abs_segs = [(x + xa_, y + ya_, x2 + xa_, y2 + ya_) for x, y, x2, y2 in segs]
                        self._draw_axis_segments(canvas, abs_segs, border_rgb, thickness=2)
            return canvas
        except Exception as e:
            ui.log('GeoTarget composite error: ' + str(e))
            return base_arr

    def _composite_product_geotarget(self, base_arr, key, meta, fallback_frames, engine_cls):
        ui = self.main_ui
        try:
            if not meta or not meta.get('targets'):
                return base_arr
            gt_f = meta.get('gt_f')
            if gt_f is None or abs(gt_f.a) < 1e-09 or abs(gt_f.e) < 1e-09:
                return base_arr
            canvas = np.ascontiguousarray(base_arr).astype(np.uint8, copy=True)
            hgt, wgt = canvas.shape[:2]
            native_w = max(1, int(round(2 * abs(gt_f.c) / abs(gt_f.a))))
            native_h = max(1, int(round(2 * abs(gt_f.f) / abs(gt_f.e))))
            scl_x = wgt / native_w
            scl_y = hgt / native_h
            eng = engine_cls if engine_cls is not None else get_engine(ui.sat_combo.currentText())
            style = self._get_anim_geo_style()
            try:
                color = ui.settings.get('geotarget_border_color', '#00E5FF')
            except Exception:
                color = '#00E5FF'
            border_rgb = self._hex_to_rgb(color)
            japan_mask = None
            for sector, bc, gt_t, crs_t, t_nc, t_bfm in meta['targets']:
                if gt_t is None or crs_t is None or abs(gt_t.a) < 1e-09 or abs(gt_t.e) < 1e-09:
                    continue
                try:
                    _bc_ids = tuple(sorted((b, id(a)) for b, a in (bc or {}).items()))
                except Exception:
                    _bc_ids = ()
                _sect_key = (key, sector, str(t_nc), 3000, _bc_ids, bool(t_bfm))
                _tile_cache = getattr(ui, '_anim_sector_tile_cache', None) or {}
                _cached_tile = _tile_cache.get(_sect_key)
                if _cached_tile is not None and _cached_tile.get('tarr') is not None:
                    tarr = _cached_tile['tarr']
                    if _cached_tile.get('gt_t') is not None:
                        gt_t = _cached_tile['gt_t']
                    thr, tw = tarr.shape[:2]
                    japan_mask = self._paste_sector_tile(
                        canvas, tarr, gt_f, gt_t, scl_x, scl_y, thr, tw,
                        border_rgb, style, sector, japan_mask)
                    continue
                try:
                    tprod = eng.composite_realtime(key, t_nc, 3000, band_cache=bc, band_file_map=None)
                except Exception:
                    tprod = None
                if tprod is None and t_nc:
                    try:
                        tprod = eng.composite_realtime(key, t_nc, 3000, band_cache=None, band_file_map=t_bfm)
                    except Exception:
                        tprod = None
                if tprod is None:
                    tarr = fallback_frames.get(sector) if fallback_frames else None
                    if tarr is None:
                        continue
                    tarr = np.ascontiguousarray(tarr).astype(np.uint8)
                else:
                    tarr = np.clip(np.asarray(tprod, dtype=np.float32), 0, 255).astype(np.uint8)
                if tarr.ndim == 2:
                    tarr = np.stack([tarr, tarr, tarr], axis=-1)
                if tarr.shape[2] == 3:
                    th, tw = tarr.shape[:2]
                    tarr = np.concatenate([tarr, np.full((th, tw, 1), 255, dtype=np.uint8)], axis=2)
                thr, tw = tarr.shape[:2]
                nav = self._read_coff_loff(t_nc) if t_nc is not None else None
                if nav is not None:
                    _cached_gt = ui._anim_target_gt_cache.get(
                        (sector, tuple(round(float(v), 3) for v in gt_t.to_gdal()), thr, tw))
                    if _cached_gt is not None:
                        gts = _cached_gt
                    else:
                        gts = self._scan_angle_target_gt(nav, gt_f, thr, tw, gt_t)
                else:
                    gts = None
                if gts is not None:
                    gt_t = gts
                    try:
                        _key = (sector, tuple(round(float(v), 3) for v in gt_t.to_gdal()), thr, tw)
                        ui._anim_target_gt_cache[_key] = gt_t
                    except Exception:
                        pass
                    try:
                        ui.log(f"Geo Target {sector}: geolocated via HSD scan angles (COFF/LOFF)")
                    except Exception:
                        pass
                else:
                    gt_t = self._resolve_target_gt(canvas, gt_f, gt_t, tarr, scl_x, scl_y, sector, thr, tw)
                japan_mask = self._paste_sector_tile(
                    canvas, tarr, gt_f, gt_t, scl_x, scl_y, thr, tw,
                    border_rgb, style, sector, japan_mask)
                if str(t_nc):
                    try:
                        if len(_tile_cache) > 512:
                            _tile_cache.clear()
                        _tile_cache[_sect_key] = {'tarr': tarr, 'gt_t': gt_t}
                    except Exception:
                        pass
            if style == 'border' and japan_mask is not None:
                ys, xs = np.where(japan_mask > 0)
                if ys.size:
                    xa_ = int(xs.min()); xb_ = int(xs.max()) + 1
                    ya_ = int(ys.min()); yb_ = int(ys.max()) + 1
                    sub = japan_mask[ya_:yb_, xa_:xb_] > 0
                    segs = self._mask_outline_segments(sub)
                    if segs:
                        abs_segs = [(x + xa_, y + ya_, x2 + xa_, y2 + ya_) for x, y, x2, y2 in segs]
                        self._draw_axis_segments(canvas, abs_segs, border_rgb, thickness=2)
            return canvas
        except Exception as e:
            try:
                ui.log('Product GeoTarget composite error: ' + str(e))
            except Exception:
                pass
            return base_arr

    def _paste_sector_tile(self, canvas, tarr, gt_f, gt_t, scl_x, scl_y, thr, tw,
                           border_rgb, style, sector, japan_mask):
        """Resize + alpha-blend a sector tile into the display canvas.

        Returns the updated Japan outline mask (or the same object when the
        tile does not contribute to it).
        """
        try:
            hgt, wgt = canvas.shape[:2]
            col0 = (gt_t.c - gt_f.c) / gt_f.a * scl_x
            row0 = (gt_f.f - gt_t.f) / -gt_f.e * scl_y
            disp_w = tw * abs(gt_t.a / gt_f.a) * scl_x
            disp_h = thr * abs(gt_t.e / gt_f.e) * scl_y
            if disp_w < 1 or disp_h < 1:
                return japan_mask
            dw = max(1, int(round(disp_w)))
            dh = max(1, int(round(disp_h)))
            t_resized = self._resize_rgba(tarr, dh, dw)
            dest_l = int(round(col0))
            dest_t = int(round(row0))
            dest_r = dest_l + dw
            dest_b = dest_t + dh
            src_l = max(0, -dest_l)
            src_t = max(0, -dest_t)
            dst_l = max(0, dest_l)
            dst_t = max(0, dest_t)
            src_r = min(dw, wgt - dst_l)
            src_b = min(dh, hgt - dst_t)
            if dest_r > wgt:
                src_r = min(src_r, dw - (dest_r - wgt))
            if dest_b > hgt:
                src_b = min(src_b, dh - (dest_b - hgt))
            if src_r <= src_l or src_b <= src_t or dst_l >= wgt or dst_t >= hgt:
                return japan_mask
            dst_r = dst_l + (src_r - src_l)
            dst_b = dst_t + (src_b - src_t)
            tile = t_resized[src_t:src_b, src_l:src_r].astype(np.float32)
            alpha = tile[:, :, 3:4] / 255.0
            region = canvas[dst_t:dst_b, dst_l:dst_r].astype(np.float32)
            blended = tile[:, :, :4] * alpha + region * (1.0 - alpha)
            canvas[dst_t:dst_b, dst_l:dst_r] = np.clip(blended, 0, 255).astype(np.uint8)
            if style == 'border' and sector.startswith('Japan:'):
                if japan_mask is None:
                    japan_mask = np.zeros((hgt, wgt), dtype=np.uint8)
                japan_mask[dst_t:dst_b, dst_l:dst_r] |= (tile[:, :, 3] > 16).astype(np.uint8)
            elif style == 'border':
                self._draw_inset_border(canvas, dst_l, dst_t, dst_r, dst_b,
                                        border_rgb, thickness=2, wlim=wgt, hlim=hgt)
            return japan_mask
        except Exception:
            return japan_mask

    def _mask_outline_segments(self, mask):
        """Trace the outline of a binary mask (True = filled) as merged
        axis-aligned grid segments (x0, y0, x1, y1) in mask space.

        Uses marching-squares on the 4-bit cell corners so the outline hugs
        the actual silhouette (e.g. the two-lobe / Z-shaped Japan area)
        instead of a plain bounding box.
        """
        h, w = mask.shape
        if h == 0 or w == 0:
            return []
        pad = np.zeros((h + 1, w + 1), dtype=np.bool_)
        pad[:h, :w] = mask
        c_tl = pad[:-1, :-1]
        c_tr = pad[:-1, 1:]
        c_bl = pad[1:, :-1]
        c_br = pad[1:, 1:]
        hsegs = []
        ys, xs = np.where(c_tl ^ c_tr)
        hsegs.extend((int(x), int(y), int(x) + 1, int(y)) for y, x in zip(ys, xs))
        ys, xs = np.where(c_bl ^ c_br)
        hsegs.extend((int(x), int(y) + 1, int(x) + 1, int(y) + 1) for y, x in zip(ys, xs))
        vsegs = []
        ys, xs = np.where(c_tl ^ c_bl)
        vsegs.extend((int(x), int(y), int(x), int(y) + 1) for y, x in zip(ys, xs))
        ys, xs = np.where(c_tr ^ c_br)
        vsegs.extend((int(x) + 1, int(y), int(x) + 1, int(y) + 1) for y, x in zip(ys, xs))
        hdict = set()
        for x0, y0, x1, y1 in hsegs:
            if x1 < x0:
                x0, x1 = x1, x0
            hdict.add((y0, x0, x1))
        vdict = set()
        for x0, y0, x1, y1 in vsegs:
            if y1 < y0:
                y0, y1 = y1, y0
            vdict.add((x0, y0, y1))
        hout = []
        byrow = {}
        for y, x0, x1 in hdict:
            byrow.setdefault(y, []).append((x0, x1))
        for y, runs in byrow.items():
            runs.sort()
            cur = None
            for a, b in runs:
                if cur is None:
                    cur = [a, b]
                else:
                    if a <= cur[1] + 1:
                        cur[1] = max(cur[1], b)
                    else:
                        hout.append((cur[0], y, cur[1], y))
                        cur = [a, b]
            if cur is not None:
                hout.append((cur[0], y, cur[1], y))
        vout = []
        bycol = {}
        for x, y0, y1 in vdict:
            bycol.setdefault(x, []).append((y0, y1))
        for x, runs in bycol.items():
            runs.sort()
            cur = None
            for a, b in runs:
                if cur is None:
                    cur = [a, b]
                else:
                    if a <= cur[1] + 1:
                        cur[1] = max(cur[1], b)
                    else:
                        vout.append((x, cur[0], x, cur[1]))
                        cur = [a, b]
            if cur is not None:
                vout.append((x, cur[0], x, cur[1]))
        return hout + vout

    def _draw_axis_segments(self, canvas, segs, rgb, thickness=2):
        hgt, wgt = canvas.shape[:2]
        r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
        hw = thickness // 2
        for x0, y0, x1, y1 in segs:
            try:
                if x0 == x1:
                    xa = max(0, x0 - hw)
                    xb = min(wgt, x0 + thickness - hw)
                    ya = max(0, min(y0, y1))
                    yb = min(hgt, max(y0, y1) + 1)
                    if xb <= xa or yb <= ya:
                        continue
                    region = canvas[ya:yb, xa:xb]
                else:
                    ya = max(0, y0 - hw)
                    yb = min(hgt, y0 + thickness - hw)
                    xa = max(0, min(x0, x1))
                    xb = min(wgt, max(x0, x1) + 1)
                    if xb <= xa or yb <= ya:
                        continue
                    region = canvas[ya:yb, xa:xb]
                region[..., 0] = r
                region[..., 1] = g
                region[..., 2] = b
                region[..., 3] = 255
            except Exception:
                pass

    def _hex_to_rgb(self, hexstr):
        try:
            v = hexstr.lstrip("#")
            if len(v) == 6:
                return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            if len(v) == 3:
                return (int(v[0] * 2, 16), int(v[1] * 2, 16), int(v[2] * 2, 16))
        except Exception:
            pass
        return (0, 229, 255)


    def _draw_inset_border(self, canvas, l, t, r, b, rgb, thickness=2, wlim=None, hlim=None):
        hgt, wgt, _ = canvas.shape
        if wlim is None:
            wlim = wgt
        if hlim is None:
            hlim = hgt
        th = max(1, int(thickness))
        for k in range(th):
            y = t + k
            if 0 <= y < hlim:
                canvas[y, max(0, l):min(wlim, r), :3] = rgb
            yb = b - 1 - k
            if 0 <= yb < hlim:
                canvas[yb, max(0, l):min(wlim, r), :3] = rgb
        for k in range(th):
            x = l + k
            if 0 <= x < wlim:
                canvas[max(0, t):min(hlim, b), x, :3] = rgb
            xr = r - 1 - k
            if 0 <= xr < wlim:
                canvas[max(0, t):min(hlim, b), xr, :3] = rgb


    def _apply_scene_overlays_to_animation(self):
        """Apply the current Scene-tab overlay settings to the animation view.

        Instead of force-enabling grid + coastlines for playback, this honours
        whatever the user has toggled in the Scene tab: coastline, grid, storm
        tracks, winds, microwave, AoR, composite/climate overlays, etc. Any
        Scene-tab state is re-projected using the animation sequence geometry
        so the animated frames show the same overlays as a normal scene view.
        """
        ui = self.main_ui
        try:
            if not hasattr(ui, 'update_overlays') or not getattr(ui, 'current_crs', None):
                return
            ui._last_overlay_key = None
            if hasattr(ui, '_remove_all_overlay_items'):
                ui._remove_all_overlay_items()
            ui.update_overlays()
            if hasattr(ui, 'graphics_view') and ui.graphics_view:
                ui.graphics_view.viewport().update()
        except Exception as e:
            ui.log(f"Apply scene overlays to animation error: {e}")


    def _on_anim_frame_ready(self, seq_idx, arr):
        ui = self.main_ui
        tb = getattr(ui, '_anim_target_base', None)
        if tb is not None and seq_idx >= tb:
            item_idx = seq_idx - tb
            info = (getattr(ui, '_anim_target_item_map', None) or {}).get(item_idx)
            if info is None:
                return
            slot, sector = info
            if 0 <= slot < len(getattr(ui, '_anim_target_frames', [])):
                if arr is not None:
                    _dr = _dc = 0.0
                    _nd = None
                    _td = getattr(ui, '_anim_target_deltas', None) or []
                    _tnd = getattr(ui, '_anim_target_native_dims', None) or []
                    if 0 <= slot < len(_td):
                        _dr, _dc = _td[slot].get(sector, (0.0, 0.0))
                    if 0 <= slot < len(_tnd):
                        _nd = _tnd[slot].get(sector)
                    arr, _dy, _dx = self._apply_frame_shift(arr, _dr, _dc, _nd)
                    ui._anim_target_frames[slot][sector] = arr
                    _tsh = getattr(ui, '_anim_target_shifts', None)
                    if _tsh is not None and 0 <= slot < len(_tsh):
                        _tsh[slot][sector] = (_dy, _dx)
                else:
                    if sector in ui._anim_target_frames[slot]:
                        ui._anim_target_frames[slot].pop(sector, None)
            ui.log(f'TARGET_READY: slot={slot} sector={sector} arr={("None" if arr is None else "shape=" + str(arr.shape))}')
            cur = getattr(ui, '_anim_index', 0)
            if slot == cur:
                frames = getattr(ui, '_anim_frames', [])
                if 0 <= cur < len(frames) and frames[cur] is not None:
                    self._display_anim_frame(cur)
            return
        fldk_map = getattr(ui, '_anim_fldk_item_to_masters', None)
        if fldk_map is not None and seq_idx in fldk_map:
            masters = fldk_map[seq_idx]
        else:
            masters = [seq_idx] if 0 <= seq_idx < len(getattr(ui, '_anim_frames', [])) else []
        shift_arr = arr
        shift_rc = (0, 0)
        if arr is not None and masters:
            _m0 = masters[0]
            _dr = _dc = 0.0
            _nd = None
            _deltas = getattr(ui, '_anim_frame_deltas', None) or []
            _dims = getattr(ui, '_anim_frame_native_dims', None) or []
            if 0 <= _m0 < len(_deltas):
                _dr, _dc = _deltas[_m0]
            if 0 <= _m0 < len(_dims):
                _nd = _dims[_m0]
            shift_arr, _dy, _dx = self._apply_frame_shift(arr, _dr, _dc, _nd)
            shift_rc = (_dy, _dx)
        _shifts = getattr(ui, '_anim_frame_shifts', None)
        for m in masters:
            if 0 <= m < len(ui._anim_frames):
                ui._anim_frames[m] = shift_arr
                if _shifts is not None and 0 <= m < len(_shifts):
                    _shifts[m] = shift_rc
        ready = sum(1 for f in ui._anim_frames if f is not None)
        total = len(ui._anim_frames) if ui._anim_frames else 0
        ui.log(f'FRAME_READY: idx={seq_idx} arr={("None" if arr is None else "shape=" + str(arr.shape))} ready={ready}/{total}')
        if hasattr(ui, 'anim_ready_label'):
            ui.anim_ready_label.setText(f'Ready: {ready} / {total} frames')
        if hasattr(ui, 'anim_progress') and ui.anim_progress.isVisible() and not getattr(ui, '_anim_geo_mode', False):
            new_loaded = ready - getattr(ui, '_anim_restored_count', 0)
            ui.anim_progress.setValue(new_loaded)
        cur = getattr(ui, '_anim_index', 0)
        if (cur in masters) or (cur == 0 and ready == 1):
            if arr is not None:
                ui._anim_just_started = True
                self._display_anim_frame(cur)
                if ui._anim_crs_list and ui._anim_crs_list[0] is not None:
                    ui.current_crs = ui._anim_crs_list[0]
                    ui.current_geotransform = ui._anim_geotransform_list[0]
                self._apply_scene_overlays_to_animation()
                return
        return

    def _on_anim_band_frame_ready(self, seq_idx, band_cache):
        ui = self.main_ui
        band_frames = getattr(ui, '_anim_band_frames', None)
        if band_frames is None:
            return
        tb = getattr(ui, '_anim_target_base', None)
        if tb is not None and seq_idx >= tb:
            item_idx = seq_idx - tb
            info = (getattr(ui, '_anim_target_item_map', None) or {}).get(item_idx)
            if info is None:
                return
            slot, sector = info
            tbf = getattr(ui, '_anim_target_band_frames', None)
            if tbf:
                if 0 <= slot < len(tbf) and band_cache:
                    tbf[slot][sector] = band_cache
                return
            return
        fldk_map = getattr(ui, '_anim_fldk_item_to_masters', None)
        if fldk_map is not None and seq_idx in fldk_map:
            masters = fldk_map[seq_idx]
        else:
            masters = [seq_idx] if 0 <= seq_idx < len(band_frames) else []
        for m in masters:
            if 0 <= m < len(band_frames):
                band_frames[m] = band_cache
        try:
            ui._apply_product_tile_availability()
        except Exception:
            pass
        if seq_idx == 0 and band_cache:
            cache = getattr(ui, 'cache', None)
            if cache is not None and hasattr(cache, 'raw'):
                entry = ui._anim_nc_paths[0] if len(getattr(ui, '_anim_nc_paths', [])) > 0 else None
                anim_nc = entry[0] if isinstance(entry, tuple) else entry
                sim_nc = getattr(ui, 'current_original', None)
                same = False
                if anim_nc is not None and sim_nc is not None:
                    try:
                        same = Path(anim_nc).resolve() == Path(sim_nc).resolve()
                    except Exception:
                        same = False
                if same:
                    for b, a in band_cache.items():
                        try:
                            cache.put_raw(b, a)
                        except Exception:
                            pass

    def _on_anim_prefetch_progress(self, cur, tot):
        # The generic 'progress' signal counts every frame across all item kinds.
        # In geo mode the per-kind bars (progress_fd / progress_target /
        # progress_japan) already report exact values, so writing the global
        # counter here would corrupt the Full Disk bar, whose maximum is only
        # the full-disk frame count.
        if getattr(self.main_ui, '_anim_geo_mode', False):
            return
        if hasattr(self.main_ui, "anim_progress") and self.main_ui.anim_progress.isVisible():
            self.main_ui.anim_progress.setMaximum(max(1, tot))
            self.main_ui.anim_progress.setValue(cur)


    def _on_anim_prefetch_progress_fd(self, cur, tot):
        if hasattr(self.main_ui, 'anim_progress') and self.main_ui.anim_progress.isVisible():
            self.main_ui.anim_progress.setMaximum(max(1, tot))
            self.main_ui.anim_progress.setValue(cur)

    def _on_anim_prefetch_progress_target(self, cur, tot):
        if hasattr(self.main_ui, 'anim_target_progress') and self.main_ui.anim_target_progress.isVisible():
            self.main_ui.anim_target_progress.setMaximum(max(1, tot))
            self.main_ui.anim_target_progress.setValue(cur)

    def _on_anim_prefetch_progress_japan(self, cur, tot):
        if hasattr(self.main_ui, 'anim_japan_progress') and self.main_ui.anim_japan_progress.isVisible():
            self.main_ui.anim_japan_progress.setMaximum(max(1, tot))
            self.main_ui.anim_japan_progress.setValue(cur)

    def _on_anim_prefetch_finished(self):
        self.main_ui.log('PREFETCH_FINISHED called')
        self._set_anim_loading(False)
        self.prefetch_finished.emit()
        if hasattr(self.main_ui, 'anim_progress'):
            self.main_ui.anim_progress.setVisible(False)
        if hasattr(self.main_ui, 'anim_target_progress'):
            self.main_ui.anim_target_progress.setVisible(False)
        if hasattr(self.main_ui, 'anim_japan_progress'):
            self.main_ui.anim_japan_progress.setVisible(False)
        ready = sum(1 for f in getattr(self.main_ui, '_anim_frames', []) if f is not None)
        total = len(getattr(self.main_ui, '_anim_frames', []))
        self.main_ui.log(f'Prefetch finished -- {ready}/{total} frames ready in RAM. Use Play, scrub, or zoom/pan live.')
        if ready > 0 and not getattr(self.main_ui, '_anim_playing', False):
            if self.main_ui._anim_crs_list and self.main_ui._anim_crs_list[0] is not None:
                self.main_ui.current_crs = self.main_ui._anim_crs_list[0]
                self.main_ui.current_geotransform = self.main_ui._anim_geotransform_list[0]
            self.main_ui._anim_just_started = True
            self._display_anim_frame(0)
            self._apply_scene_overlays_to_animation()
        if getattr(self.main_ui, '_anim_drop_autoplay', False):
            self.main_ui._anim_drop_autoplay = False
            if ready > 0:
                self.main_ui._anim_playing = True
                if hasattr(self.main_ui, '_anim_timer'):
                    self.main_ui._anim_timer.start(self._compute_anim_interval())
                if hasattr(self.main_ui, 'anim_play_btn'):
                    self.main_ui.anim_play_btn.setText('⏸ Pause')
                self.main_ui._sync_viewport_anim_bar()
                self.main_ui.log('Drop animation: playback started.')
                return
            return
        return

    def _advance_animation(self):

        """Advance to next animation frame.

        Moves to the next frame in the sequence and updates the
        display. Loops back to start if at end of sequence
        (unless loop mode is disabled).

        Side Effects:
            - Emits frame_changed signal
            - Updates viewport image
            - Updates frame counter display
        """
        if not getattr(self.main_ui, "_anim_playing", False):
            if hasattr(self.main_ui, "_anim_timer"):
                self.main_ui._anim_timer.stop()
            return
        frames = getattr(self.main_ui, "_anim_frames", [])
        total = len(frames)
        if total == 0:
            self._toggle_animation()
            return

        start = self.main_ui._anim_index
        idx = start
        found = False
        for _ in range(total):
            idx = (idx + 1) % total
            if frames[idx] is not None:
                found = True
                break
        if not found:
            self.main_ui.log("No more ready frames.")
            self.main_ui._anim_playing = False
            if hasattr(self.main_ui, "_anim_timer"):
                self.main_ui._anim_timer.stop()
            if hasattr(self.main_ui, "anim_play_btn"):
                self.main_ui.anim_play_btn.setText("\u25B6 Play")
            return
        self.main_ui._anim_index = idx
        self._display_anim_frame(idx)

        if not getattr(self.main_ui, "anim_loop_cb", None) or not self.main_ui.anim_loop_cb.isChecked():
            last_ready = -1
            for i in range(total-1, -1, -1):
                if frames[i] is not None:
                    last_ready = i
                    break
            if idx >= last_ready or idx < start:
                self.main_ui._anim_playing = False
                if hasattr(self.main_ui, "_anim_timer"):
                    self.main_ui._anim_timer.stop()
                if hasattr(self.main_ui, "anim_play_btn"):
                    self.main_ui.anim_play_btn.setText("\u25B6 Play")
                self.main_ui.log("Sequence end reached (Loop off).")


    def _load_animation_sequence(self):

        """Load animation frames for date range.

        Retrieves satellite imagery for the specified dates and
        band. Initiates prefetching for smooth playback. Shows
        progress dialog during loading.

        Preserves already-cached frames from previous prefetch so
        extending the From/To range only loads new frames.

        Args:
            dates (list[datetime]): Date range for animation.
            band (str): Spectral band identifier.
            satellite_type (str): Platform ('himawari' or 'goes').

        Returns:
            bool: True if loading succeeded, False otherwise.

        Side Effects:
            - Updates animation frame list
            - Initiates background prefetching
            - Shows/hides loading dialog
        """
        self.main_ui.log("Animation: Generating timestamps + starting prefetch using cache workers / RGB engine...")
        timestamps = self._generate_animation_timestamps()
        if not timestamps:
            self.main_ui.log("No valid timestamps in range (check From/To and Max frames).")
            return

        # Snapshot old cached data before cancelling
        old_timestamps = getattr(self.main_ui, '_anim_timestamps', [])
        old_frames = getattr(self.main_ui, '_anim_frames', [])
        old_band_frames = getattr(self.main_ui, '_anim_band_frames', [])
        old_nc_paths = getattr(self.main_ui, '_anim_nc_paths', [])
        old_crs_list = getattr(self.main_ui, '_anim_crs_list', [])
        old_gt_list = getattr(self.main_ui, '_anim_geotransform_list', [])
        old_map = {}
        old_target_frames = {}
        old_target_bframes = {}
        old_target_crs = {}
        old_target_gt = {}
        old_tgt_frames = getattr(self.main_ui, '_anim_target_frames', []) or []
        old_tgt_bframes = getattr(self.main_ui, '_anim_target_band_frames', []) or []
        old_tgt_crs = getattr(self.main_ui, '_anim_target_crs', []) or []
        old_tgt_gt = getattr(self.main_ui, '_anim_target_gt', []) or []
        for i, ts in enumerate(old_timestamps):
            if i < len(old_frames) and old_frames[i] is not None:
                nc_entry = old_nc_paths[i] if i < len(old_nc_paths) else None
                crs = old_crs_list[i] if i < len(old_crs_list) else None
                gt = old_gt_list[i] if i < len(old_gt_list) else None
                bc = old_band_frames[i] if i < len(old_band_frames) else None
                old_map[ts] = (old_frames[i], bc, nc_entry, crs, gt)
            if i < len(old_tgt_frames) and old_tgt_frames[i]:
                old_target_frames[ts] = old_tgt_frames[i]
            if i < len(old_tgt_bframes) and old_tgt_bframes[i]:
                old_target_bframes[ts] = old_tgt_bframes[i]
            if i < len(old_tgt_crs) and old_tgt_crs[i]:
                old_target_crs[ts] = old_tgt_crs[i]
            if i < len(old_tgt_gt) and old_tgt_gt[i]:
                old_target_gt[ts] = old_tgt_gt[i]

        self._cancel_anim_prefetch()
        self._set_anim_loading(True)
        self.main_ui._anim_target_gt_cache = {}
        self.main_ui._anim_composite_cache = {}
        self.main_ui._anim_composite_order = []
        self._composite_cache_bytes = 0
        self.main_ui._anim_sector_tile_cache = {}
        self.main_ui._anim_last_track_cx = None
        self.main_ui._anim_last_track_cy = None

        geo_mode = False
        geo_sectors = []
        geo_style = 'border'
        if hasattr(self.main_ui, 'anim_type_combo'):
            geo_mode = self._is_geo_target_text(self.main_ui.anim_type_combo.currentText())
        if geo_mode:
            geo_sectors = list(self._get_anim_geo_sectors())
            geo_style = self._get_anim_geo_style() or 'border'

        is_rgb, selector = self.main_ui._get_anim_selected_content()
        ctype, csel = self.main_ui._get_anim_active_content()
        checked_bands = tuple(sorted(self.main_ui._get_anim_checked_bands()))
        content_sig = (ctype, csel, checked_bands, is_rgb, selector)
        prev_sig = getattr(self.main_ui, '_anim_content_signature', None)
        content_changed = prev_sig is not None and prev_sig != content_sig

        # Base type signature: the animation master frames' identity. When this
        # changes (e.g. Target --> Geo Target / Full GEO, or FLDK <-> Japan, or
        # switching satellites) the previously cached master frames/nc allocation
        # no longer describe this sequence, so they must be re-derived instead of
        # being restored by timestamp.
        try:
            _sat_sig = (self.main_ui.anim_sat_combo.currentData()
                        or self.main_ui.anim_sat_combo.currentText())
        except Exception:
            _sat_sig = ""
        _prod_sig = self.get_anim_himawari_product() if hasattr(self, 'get_anim_himawari_product') else 'AHI-L1b-FLDK'
        base_sig = (str(_sat_sig), str(_prod_sig))
        prev_base_sig = getattr(self.main_ui, '_anim_base_signature', None)
        base_changed = prev_base_sig is not None and prev_base_sig != base_sig
        if base_changed:
            self.main_ui.log(f"Animation base type changed ({prev_base_sig!r} -> {base_sig}) - re-deriving master frames / allocation from disk instead of reusing the previous cache.")

        if content_changed:
            _parts = []
            _names = ('active content', 'checked bands', 'rgb mode', 'band selector')
            try:
                for _nm, _x, _y in zip(_names, prev_sig, content_sig):
                    if _x != _y:
                        _parts.append(_nm)
                if len(prev_sig) != len(content_sig):
                    _parts.append('signature format')
            except Exception:
                _parts = ['unknown']
            self.main_ui.log('Content selection changed (' + (", ".join(_parts) or "unknown") + ') - keeping raw band caches; frames will re-render for the new content.')
            for _ts in list(old_map.keys()):
                _fr, _bc, _nc, _crs, _gt = old_map[_ts]
                old_map[_ts] = (None, _bc, _nc, _crs, _gt)
            old_target_frames.clear()
            old_target_bframes.clear()
            old_target_crs.clear()
            old_target_gt.clear()

        self.main_ui._anim_timestamps = timestamps[:]
        self.main_ui._anim_frames = [None] * len(timestamps)
        self.main_ui._anim_band_frames = [None] * len(timestamps)
        self.main_ui._anim_nc_paths = [None] * len(timestamps)
        self.main_ui._anim_crs_list = [None] * len(timestamps)
        self.main_ui._anim_geotransform_list = [None] * len(timestamps)
        self.main_ui._anim_content_signature = content_sig
        self.main_ui._anim_base_signature = base_sig
        self.main_ui._anim_index = 0
        self.main_ui._anim_playing = False
        self.main_ui._last_shown_valid = None
        self.main_ui._anim_geo_mode = geo_mode
        self.main_ui._anim_target_frames = [{} for _ in timestamps]
        self.main_ui._anim_target_crs = [{} for _ in timestamps]
        self.main_ui._anim_target_gt = [{} for _ in timestamps]
        self.main_ui._anim_target_nc = [[] for _ in timestamps]
        self.main_ui._anim_target_band_frames = [{} for _ in timestamps]
        self.main_ui._anim_target_item_map = {}
        self.main_ui._anim_target_base = 10000000 if geo_mode else None
        self.main_ui._anim_fldk_item_to_masters = None
        self.main_ui._anim_sync_product_idx = None
        self.main_ui._anim_sync_target_meta = None
        self.main_ui._anim_sync_pending_idx = None
        self._scene_prod_sync_sig = None
        self._scene_prod_sync_at = 0.0
        self._scene_prod_sync_prod = None
        if hasattr(self.main_ui, 'anim_play_btn'):
            self.main_ui.anim_play_btn.setText("\u25B6 Play")

        restored = 0
        restored_frames = 0
        for i, ts in enumerate(timestamps):
            if ts in old_map:
                frame, bc, nc_entry, crs, gt = old_map[ts]
                if not base_changed:
                    if frame is not None:
                        self.main_ui._anim_frames[i] = frame
                        restored_frames += 1
                    self.main_ui._anim_band_frames[i] = bc
                    self.main_ui._anim_nc_paths[i] = nc_entry
                    self.main_ui._anim_crs_list[i] = crs
                    self.main_ui._anim_geotransform_list[i] = gt
                    restored += 1
            if content_changed or base_changed:
                continue
            if ts not in old_target_frames or not old_target_frames[ts]:
                continue
            self.main_ui._anim_target_frames[i] = old_target_frames[ts]
            if ts in old_target_bframes:
                self.main_ui._anim_target_band_frames[i] = old_target_bframes[ts]
            if ts in old_target_crs:
                self.main_ui._anim_target_crs[i] = old_target_crs[ts]
            if ts in old_target_gt:
                self.main_ui._anim_target_gt[i] = old_target_gt[ts]
        self.main_ui._anim_restored_count = restored
        if restored_frames:
            self.main_ui.log(f"Reusing {restored_frames} already-cached frame(s) from previous prefetch (only loading new ones).")
        elif restored:
            self.main_ui.log(f"Reusing {restored} cached data slot(s) from previous prefetch (re-rendering frames for the new content).")
        else:
            self.main_ui.log("Loading animation data from NC files...")

        if hasattr(self.main_ui, 'anim_sat_combo') and self.main_ui.anim_sat_combo.currentData():
            anim_sat = self.main_ui.anim_sat_combo.currentData()
        elif hasattr(self.main_ui, 'anim_sat_combo'):
            anim_sat = self.main_ui.anim_sat_combo.currentText()
        else:
            anim_sat = 'himawari9'
        if hasattr(self, 'get_anim_himawari_product'):
            product = self.get_anim_himawari_product()
        else:
            product = 'AHI-L1b-FLDK'
        base = Path(self.main_ui.input_dir) / anim_sat

        for i, ts in enumerate(timestamps):
            if self.main_ui._anim_nc_paths[i] is not None:
                continue
            nc = None
            bfm = None
            if base.exists():
                ts_variants = [ts]
                parts = ts.rsplit('_', 1)
                if len(parts) == 2:
                    compact_ts = parts[0].replace('_', '') + '_' + parts[1]
                    if compact_ts != ts:
                        ts_variants.append(compact_ts)
                if 'goes' in anim_sat.lower():
                    try:
                        dt_ts = datetime.strptime(ts, '%Y_%m_%d_%H%M')
                        doy = dt_ts.timetuple().tm_yday
                        hh = dt_ts.strftime('%H')
                        goes_doy_ts = f"{dt_ts.year}_{doy:03d}_{hh}"
                        if goes_doy_ts not in ts_variants:
                            ts_variants.append(goes_doy_ts)
                    except ValueError:
                        pass
                matches = []
                for tsv in ts_variants:
                    matches = list(base.glob('*' + tsv + '*'))
                    if matches:
                        break
                if not matches and ('gk2a' in anim_sat.lower() or 'gk-2a' in anim_sat.lower()):
                    # GK-2A scenes live under nested AMI product folders (AMI/L1B/FD_YYYYMMDD_HHMM
                    # or gk2a-pds/AMI/L1B/FD_YYYY_MM_DD_HHMM) — locate them by the trailing
                    # _YYYYMMDDHHMM scan time in the gk2a_*.nc filenames.
                    scan_ts = ts.replace('_', '')
                    scene_dirs = {}
                    for f in Path(self.main_ui.input_dir).rglob("gk2a_*.nc"):
                        m_gk = _re.search(r'_(\d{12})\.nc$', f.name)
                        if m_gk and m_gk.group(1) == scan_ts:
                            scene_dirs[str(f.parent)] = f.parent
                    matches = list(scene_dirs.values())
                if not matches and 'himawari' in anim_sat.lower() and product in ('AHI-L1b-Japan', 'AHI-L1b-Target'):
                    hm = ts.rsplit('_', 1)[-1]
                    for d in base.iterdir():
                        if not d.is_dir():
                            continue
                        if product not in d.name:
                            continue
                        for f in d.glob('*.nc'):
                            m_rapid = _re.search(r'_(\d{4})(\d{2})(\d{2})_(\d{6})', f.name)
                            if not m_rapid:
                                continue
                            f_ts = f"{m_rapid.group(1)}_{m_rapid.group(2)}_{m_rapid.group(3)}_{m_rapid.group(4)[:4]}"
                            if f_ts == ts:
                                matches = [f]
                                break
                        if matches:
                            break
                if 'himawari' in anim_sat.lower():
                    matches = [m for m in matches if product in m.name]
                if geo_mode and 'goes' in anim_sat.lower():
                    matches = [m for m in matches if 'ABI-L1b-RadF' in m.name]
                for m in matches:
                    pattern = _get_nc_glob_pattern(anim_sat)
                    if m.is_dir():
                        hits = list(m.glob(pattern))
                        if not hits and 'goes' in anim_sat.lower():
                            for fb in ('*C*.nc', '*.nc'):
                                hits = list(m.glob(fb))
                                if hits:
                                    break
                    else:
                        if m.suffix.lower() == '.nc':
                            hits = [m]
                        else:
                            hits = []
                    if hits and 'himawari' in anim_sat.lower() and product == 'AHI-L1b-Target' and len(hits) > 1:
                        try:
                            slot_dt = datetime.strptime(ts, '%Y_%m_%d_%H%M')
                        except ValueError:
                            slot_dt = None
                        if slot_dt is not None:
                            best_hd = float('inf')
                            best_h = None
                            for h in hits:
                                obs = self._target_obs_utc(h)
                                if obs is None:
                                    continue
                                hd = abs(obs - slot_dt).total_seconds()
                                if hd < best_hd:
                                    best_h = h
                                    best_hd = hd
                            if best_h is not None:
                                hits = [best_h]
                    if hits and 'goes' in anim_sat.lower():
                        try:
                            dt_ts = datetime.strptime(ts, '%Y_%m_%d_%H%M')
                            doy = dt_ts.timetuple().tm_yday
                            target_scan = f"{dt_ts.year}{doy:03d}{dt_ts.strftime('%H%M')}"
                            scan_groups = {}
                            for h in hits:
                                sm = _re.search(r'_s(\d{4})(\d{3})(\d{2})(\d{2})\d{3}', h.name)
                                if not sm:
                                    continue
                                scan_key = f"{sm.group(1)}{sm.group(2)}{sm.group(3)}{sm.group(4)}"
                                scan_groups.setdefault(scan_key, []).append(h)
                            if scan_groups:
                                if target_scan in scan_groups:
                                    hits = scan_groups[target_scan]
                                else:
                                    def _scan_diff(key):
                                        try:
                                            ft = datetime.strptime(key, '%Y%j%H%M')
                                        except ValueError:
                                            return float('inf')
                                        return abs(ft - dt_ts).total_seconds()
                                    best_key = min(scan_groups, key=_scan_diff)
                                    hits = scan_groups[best_key]
                        except ValueError:
                            pass
                    if not hits:
                        continue
                    per_band = [h for h in hits if '_B' in h.name]
                    if not per_band and 'goes' in anim_sat.lower():
                        per_band = [h for h in hits if _re.search(r'C(\d{2})', h.name)]
                    is_gk2a = 'gk2a' in anim_sat.lower() or 'gk-2a' in anim_sat.lower()
                    if is_gk2a:
                        from src.parsers.gk2a_parser import extract_gk2a_bands
                        bfm = extract_gk2a_bands(hits)
                    if per_band:
                        bfm = {}
                        for h in per_band:
                            bm = _re.search(r'C(\d{2})', h.name)
                            if bm and 'goes' in anim_sat.lower():
                                c_num = int(bm.group(1))
                                if c_num == 2:
                                    bfm['B03'] = h
                                elif c_num == 3:
                                    bfm['B02'] = h
                                else:
                                    bfm['B' + f'{c_num:02d}'] = h
                                bfm['C' + f'{c_num:02d}'] = h
                            else:
                                bm = _re.search(r'_B(\d{2})_', h.name)
                                if bm:
                                    bfm['B' + str(bm.group(1))] = h
                    if is_rgb:
                        nc = hits[0]
                    elif ('goes' in anim_sat.lower() or is_gk2a) and bfm and selector in bfm:
                        nc = bfm[selector]
                    else:
                        nc = hits[0]
                    self.main_ui._anim_nc_paths[i] = (nc, bfm) if bfm else nc

        for i, entry in enumerate(self.main_ui._anim_nc_paths):
            if self.main_ui._anim_crs_list[i] is not None:
                continue
            _c, _g = None, None
            if entry is not None:
                nc_path = entry[0] if isinstance(entry, tuple) else entry
                _c, _g = self.main_ui.extract_crs_from_ads(nc_path)
            self.main_ui._anim_crs_list[i] = _c
            self.main_ui._anim_geotransform_list[i] = _g

        total = len(timestamps)
        actual_data = sum(1 for p in self.main_ui._anim_nc_paths if p is not None)
        max_px = getattr(self.main_ui, 'preview_max_px', 2200)
        if self.main_ui._anim_geo_mode:
            self._build_geo_targets(anim_sat, timestamps, is_rgb, selector, max_px)
        self._setup_anim_registration()
        self.main_ui.log(f"Sequence built: {total} time slots in selected range. {actual_data} actually have NC files on disk.")
        if actual_data < total:
            self.main_ui.log(f"Note: This range is sparse (common for Japan/Target rapid scan). Using product={product}. Only available frames will be shown.")
        if self.main_ui._anim_geo_mode:
            tgt_hits = sum(len(v) for v in self.main_ui._anim_target_nc)
            self.main_ui.log(f"Geo Target: {tgt_hits} target-area frame(s) located across the timeline (sectors={self._get_anim_geo_sectors()}).")
        if hasattr(self.main_ui, 'anim_frame_slider'):
            self.main_ui.anim_frame_slider.setRange(0, max(0, total - 1))
            self.main_ui.anim_frame_slider.setValue(0)
        if hasattr(self.main_ui, 'anim_frame_label'):
            self.main_ui.anim_frame_label.setText(f"Frame: 1 / {total}")
        ready_count = sum(1 for f in self.main_ui._anim_frames if f is not None)
        if hasattr(self.main_ui, 'anim_ready_label'):
            self.main_ui.anim_ready_label.setText(f"Ready: {ready_count} / {total} frames")

        load_items = []
        ac = (ctype, csel)
        cb_bands = checked_bands or ((csel,) if ctype == 'BAND' and csel else ())
        max_prefetch = getattr(self.main_ui, 'anim_max_prefetch', None)
        if hasattr(self.main_ui, 'anim_max_prefetch') and hasattr(self.main_ui.anim_max_prefetch, 'value'):
            max_prefetch = self.main_ui.anim_max_prefetch.value()
        limit_enabled = getattr(self.main_ui, 'anim_limit_frames', None) and self.main_ui.anim_limit_frames.isChecked()
        fd_budget = 0
        kind_hint = self._anim_kind_hint()
        item_kinds = []
        for i, entry in enumerate(self.main_ui._anim_nc_paths):
            if entry is None:
                continue
            if self.main_ui._anim_frames[i] is not None:
                continue
            if isinstance(entry, tuple):
                nc_path, bfm = entry
            else:
                nc_path, bfm = entry, None
            load_items.append((i, nc_path, cb_bands, ac, max_px, bfm))
            item_kinds.append(kind_hint)
            fd_budget += 1
            if limit_enabled and fd_budget >= max_prefetch:
                break
        if self.main_ui._anim_geo_mode:
            # 'Max frames' is applied separately per frame type (FLDK / Target / Japan),
            # so each category gets its own budget instead of one combined total.
            tb = self.main_ui._anim_target_base
            item_map = self.main_ui._anim_target_item_map
            target_max_px = max(2048, int(max_px))
            tgt_budget = {}
            for i in range(len(timestamps)):
                for sector, t_nc, t_bfm in self.main_ui._anim_target_nc[i]:
                    if sector in self.main_ui._anim_target_frames[i]:
                        continue
                    kind = self._kind_for_sector(sector)
                    used = tgt_budget.get(kind, 0)
                    if limit_enabled and used >= max_prefetch:
                        continue
                    item_idx = len(item_map)
                    item_map[item_idx] = (i, sector)
                    load_items.append((tb + item_idx, t_nc, cb_bands, ac, target_max_px, t_bfm))
                    item_kinds.append(kind)
                    tgt_budget[kind] = used + 1

        if not load_items:
            if restored == total:
                self.main_ui.log("All frames are already cached in RAM. Nothing new to prefetch.")
            else:
                self.main_ui.log("No loadable NC files in the selected range. Ensure data exists for the times (run bg_to_nc if needed).")
            if hasattr(self.main_ui, 'anim_progress'):
                self.main_ui.anim_progress.setVisible(False)
            if hasattr(self.main_ui, 'anim_target_progress'):
                self.main_ui.anim_target_progress.setVisible(False)
            if hasattr(self.main_ui, 'anim_japan_progress'):
                self.main_ui.anim_japan_progress.setVisible(False)
            if self.main_ui._anim_crs_list and self.main_ui._anim_crs_list[0] is not None:
                self.main_ui.current_crs = self.main_ui._anim_crs_list[0]
                self.main_ui.current_geotransform = self.main_ui._anim_geotransform_list[0]
            self.main_ui._last_overlay_key = None
            self.main_ui._anim_just_started = True
            self._display_anim_frame(0)
            return

        if limit_enabled:
            _frames = self.main_ui._anim_frames
            _pending_fd = sum(
                1 for i, p in enumerate(self.main_ui._anim_nc_paths)
                if p is not None and (i >= len(_frames) or _frames[i] is None)
            )
            _pending_tgt = 0
            if self.main_ui._anim_geo_mode:
                for i, slots in enumerate(self.main_ui._anim_target_nc):
                    _tf = self.main_ui._anim_target_frames[i] if i < len(self.main_ui._anim_target_frames) else {}
                    _pending_tgt += sum(1 for _s, _n, _b in slots if _s not in _tf)
            if len(load_items) < (_pending_fd + _pending_tgt):
                if self.main_ui._anim_geo_mode:
                    self.main_ui.log(
                        f"Timeline has {len(self.main_ui._anim_timestamps)} slots. 'Max frames' ({max_prefetch}) "
                        f"is applied separately per frame type (FLDK / Target / Japan), so each category "
                        f"prefetched up to its own budget ({len(load_items)} new frames total; "
                        f"{_pending_fd} FLDK + {_pending_tgt} target frames available). "
                        f"You can still scrub the full timeline; later frames load on demand when you reach them."
                    )
                else:
                    self.main_ui.log(
                        f"Timeline has {len(self.main_ui._anim_timestamps)} slots. Only prefetching the first "
                        f"{len(load_items)} new frames into RAM (Max frames = {max_prefetch}). "
                        f"You can still scrub the full timeline; later frames will load on demand when you reach them."
                    )

        tb = self.main_ui._anim_target_base
        fd_count = 0
        tgt_count = 0
        jpn_count = 0
        for k in item_kinds:
            if k == 'japan':
                jpn_count += 1
            elif k == 'target':
                tgt_count += 1
            else:
                fd_count += 1
        if hasattr(self.main_ui, 'anim_progress'):
            self.main_ui.anim_progress.setVisible(fd_count > 0)
            self.main_ui.anim_progress.setMaximum(max(1, fd_count))
            self.main_ui.anim_progress.setValue(0)
        if hasattr(self.main_ui, 'anim_target_progress'):
            self.main_ui.anim_target_progress.setVisible(tgt_count > 0)
            self.main_ui.anim_target_progress.setMaximum(max(1, tgt_count))
            self.main_ui.anim_target_progress.setValue(0)
        if hasattr(self.main_ui, 'anim_japan_progress'):
            self.main_ui.anim_japan_progress.setVisible(jpn_count > 0)
            self.main_ui.anim_japan_progress.setMaximum(max(1, jpn_count))
            self.main_ui.anim_japan_progress.setValue(0)
        if hasattr(self.main_ui, 'anim_sat_combo'):
            _sat_anim = self.main_ui.anim_sat_combo.currentText()
        else:
            _sat_anim = 'himawari9'
        _eng_anim = get_engine(_sat_anim)
        self.main_ui._anim_prefetch_worker = AnimationPrefetchWorker(
            load_items, engine_class=_eng_anim, target_item_base=tb,
            item_kinds=item_kinds)
        self.main_ui._anim_prefetch_thread = QThread()
        self.main_ui._anim_prefetch_worker.moveToThread(self.main_ui._anim_prefetch_thread)
        self.main_ui._anim_prefetch_worker.frame_ready.connect(self._on_anim_frame_ready)
        self.main_ui._anim_prefetch_worker.band_frame_ready.connect(self._on_anim_band_frame_ready)
        self.main_ui._anim_prefetch_worker.progress.connect(self._on_anim_prefetch_progress)
        self.main_ui._anim_prefetch_worker.progress_fd.connect(self._on_anim_prefetch_progress_fd)
        self.main_ui._anim_prefetch_worker.progress_target.connect(self._on_anim_prefetch_progress_target)
        self.main_ui._anim_prefetch_worker.progress_japan.connect(self._on_anim_prefetch_progress_japan)
        self.main_ui._anim_prefetch_worker.finished.connect(self._on_anim_prefetch_finished)
        self.main_ui._anim_prefetch_thread.started.connect(self.main_ui._anim_prefetch_worker.run)
        self.main_ui._anim_prefetch_worker.finished.connect(self.main_ui._anim_prefetch_thread.quit)
        self.main_ui._anim_prefetch_worker.finished.connect(self.main_ui._anim_prefetch_worker.deleteLater)
        self.main_ui._anim_prefetch_thread.finished.connect(self.main_ui._anim_prefetch_thread.deleteLater)
        self.main_ui._anim_just_started = True
        self.main_ui._anim_prefetch_thread.start()
        self.main_ui.log(f"Prefetch worker launched ({len(load_items)} new frames to load, parallel I/O). Play will work on ready frames; zoom/pan live during animation.")
        return

    def _build_geo_targets(self, anim_sat, timestamps, is_rgb, selector, max_px):
        """Locate target-area (Meso1/Meso2/Target) NC files per animation slot.

        Also builds the FLDK carry-forward mapping so master slots that have no
        full-disk scan inherit the nearest earlier full-disk frame.
        """
        ui = self.main_ui
        n = len(timestamps)
        base = Path(ui.input_dir) / anim_sat

        fldk_map = {}
        last_owner = None
        for i in range(n):
            if ui._anim_nc_paths[i] is not None:
                fldk_map.setdefault(i, []);
                fldk_map[i].append(i)
                last_owner = i
            else:
                if last_owner is not None:
                    fldk_map.setdefault(last_owner, []).append(i)
                    ui._anim_crs_list[i] = ui._anim_crs_list[last_owner]
                    ui._anim_geotransform_list[i] = ui._anim_geotransform_list[last_owner]
        ui._anim_fldk_item_to_masters = fldk_map or None

        for owners, masters in fldk_map.items():
            if ui._anim_frames[owners] is not None:
                for m in masters:
                    if m != owners and ui._anim_frames[m] is None:
                        ui._anim_frames[m] = ui._anim_frames[owners]

        sectors = list(self._get_anim_geo_sectors())
        if not sectors or not base.exists():
            return

        is_goes = "goes" in anim_sat.lower()

        for i, ts in enumerate(timestamps):
            found = self._find_geo_target_nc(base, anim_sat, ts, sectors, is_rgb, selector, is_goes)
            if not found:
                continue
            ui._anim_target_nc[i] = found
            for sector, t_nc, t_bfm in found:
                if t_nc is None:
                    continue
                try:
                    _c, _g = ui.extract_crs_from_ads(t_nc)
                except Exception:
                    _c, _g = None, None
                if _c and _g:
                    ui._anim_target_crs[i][sector] = _c
                    ui._anim_target_gt[i][sector] = _g
        return


    def _find_geo_target_nc(self, base, anim_sat, ts, sectors, is_rgb, selector, is_goes):
        results = []
        try:
            dt_ts = datetime.strptime(ts, '%Y_%m_%d_%H%M')
        except ValueError:
            return results
        if is_goes:
            doy = dt_ts.timetuple().tm_yday
            hh = dt_ts.strftime('%H')
            goes_prod = {'M1': 'ABI-L1b-RadM1', 'M2': 'ABI-L1b-RadM2', 'C': 'ABI-L1b-RadC'}
            for sector in sectors:
                prod = goes_prod.get(sector)
                if not prod:
                    continue
                d = base / f"{prod}_{dt_ts.year}_{doy:03d}_{hh}"
                if not d.is_dir():
                    continue
                hits = []
                for fb in ('*C*.nc', '*.nc'):
                    hits = list(d.glob(fb))
                    if hits:
                        break
                if not hits:
                    continue
                scan_groups = {}
                for h in hits:
                    sm = _re.search(r'_s(\d{4})(\d{3})(\d{2})(\d{2})\d{3}', h.name)
                    if not sm:
                        continue
                    key = f"{sm.group(1)}{sm.group(2)}{sm.group(3)}{sm.group(4)}"
                    scan_groups.setdefault(key, []).append(h)
                if scan_groups:
                    target_scan = f"{dt_ts.year}{doy:03d}{dt_ts.strftime('%H%M')}"
                    if target_scan in scan_groups:
                        hits = scan_groups[target_scan]
                    else:
                        best_key = min(scan_groups, key=lambda k: self._scan_ts_diff(k, target_scan))
                        hits = scan_groups[best_key]
                bfm, t_nc = self._build_band_map_for_hits(hits, anim_sat, is_rgb, selector)
                if t_nc is None:
                    continue
                results.append((sector, t_nc, bfm))
            return results
        tol = timedelta(minutes=8)
        # Japan first so the Target rapid-scan (finer) pastes on top of it.
        for sector in sectors:
            if sector != 'Japan':
                continue
            best_d = None
            best_dd = float('inf')
            for d in base.glob('AHI-L1b-Japan_*'):
                if not d.is_dir():
                    continue
                ft = self._folder_ts(d.name)
                if ft is None:
                    continue
                dd = abs((ft - dt_ts).total_seconds())
                if dd > tol.total_seconds():
                    continue
                if dd < best_dd:
                    best_dd = dd
                    best_d = d
            if best_d is None:
                continue
            tiles = {}
            for h in best_d.glob('*.nc'):
                jm = _re.search(r'_JP(\d{2})(?:[._]|$)', h.name)
                if jm:
                    tiles.setdefault(jm.group(1), []).append(h)
            for jp in sorted(tiles):
                bfm, t_nc = self._build_band_map_for_hits(tiles[jp], anim_sat, is_rgb, selector)
                if t_nc is None:
                    continue
                results.append((f'Japan:{jp}', t_nc, bfm))
        for sector in sectors:
            if sector != 'Target':
                continue
            best_h = None
            best_hd = float('inf')
            for d in base.glob('AHI-L1b-Target_*'):
                if not d.is_dir():
                    continue
                for h in d.glob('*.nc'):
                    obs = self._target_obs_utc(h)
                    if obs is None:
                        continue
                    hd = abs((obs - dt_ts).total_seconds())
                    if hd > tol.total_seconds():
                        continue
                    if hd < best_hd:
                        best_hd = hd
                        best_h = h
            if best_h is None:
                continue
            bfm, t_nc = self._build_band_map_for_hits([best_h], anim_sat, is_rgb, selector)
            if t_nc is None:
                continue
            results.append((sector, t_nc, bfm))
        return results

    def _scan_ts_diff(self, key, target_scan):
        try:
            ft = datetime.strptime(key, "%Y%j%H%M")
            tt = datetime.strptime(target_scan, "%Y%j%H%M")
            return abs((ft - tt).total_seconds())
        except Exception:
            return float("inf")

    def _folder_ts(self, name):
        """Derive a nominal datetime from an AHI/ABI folder token:
        AHI  -> _2026_08_16_0200   (year_month_day_hhmm)
        ABI  -> _2026_229_02       (year_doy_hour)
        """
        try:
            m = _re.search(r'_(\d{4})_(\d{2})_(\d{2})_(\d{2})(\d{2})', name)
            if m:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                int(m.group(4)), int(m.group(5)))
            m = _re.search(r'_(\d{4})_(\d{3})_(\d{2})', name)
            if m:
                doy = m.group(2).zfill(3)
                return datetime.strptime(f"{m.group(1)}-{doy}-{m.group(3)}", "%Y-%j-%H")
        except Exception:
            pass
        return None

    def _find_fl_dk_nc(self, nc_path):
        """Locate the nearest full-disk NC for a sub-area NC (Himawari Target/Japan
        or GOES Meso) so scene-time placement can be data-validated against the
        disk. Returns a Path or None."""
        try:
            base = Path(getattr(self.main_ui, 'input_dir', ''))
            if not base.is_dir():
                return None
            anim_sat = ""
            try:
                anim_sat = (self.main_ui.anim_sat_combo.currentData()
                            or self.main_ui.anim_sat_combo.currentText())
            except Exception:
                pass
            if not anim_sat:
                anim_sat = "goes16" if ("ABI" in str(nc_path).upper()) else "himawari9"
            sdir = base / anim_sat
            if not sdir.is_dir():
                return None
            is_goes = "goes" in str(anim_sat).lower() or "ABI" in str(nc_path).upper()
            obs = None
            try:
                obs = self._target_obs_utc(nc_path)
            except Exception:
                pass
            if obs is None:
                try:
                    m = _re.search(r'_s(\d{4})(\d{3})(\d{2})(\d{2})\d{3}', str(nc_path))
                    if m:
                        obs = datetime.strptime(f"{m.group(1)}-{m.group(2).zfill(3)}-{m.group(3)}{m.group(4)}",
                                                "%Y-%j-%H%M")
                except Exception:
                    pass
            if obs is None:
                return None
            pat = "ABI-L1b-RadF_*" if is_goes else "AHI-L1b-FLDK_*"
            best, best_d = None, float('inf')
            for d in sdir.glob(pat):
                if not d.is_dir():
                    continue
                t = self._folder_ts(d.name)
                if t is None:
                    continue
                dt = abs((t - obs).total_seconds())
                if dt < best_d:
                    best_d, best = dt, d
            if best is None or best_d > 15 * 60:
                return None
            hits = list(best.glob("*.nc"))
            if not hits:
                return None
            b13 = next((h for h in hits if "_B13" in h.name.upper()
                        or "B13" in h.name.upper()), None)
            if b13 is not None:
                return b13
            return hits[0]
        except Exception:
            return None

    def _scene_geo_reference(self, nc_path):
        """Load + cache the full-disk reference used to validate a sub-area
        geotransform for scene placement. Returns (ref_rgb, ref_gt) or (None, None)."""
        ui = self.main_ui
        cache = getattr(ui, '_scene_fl_dk_ref_cache', None)
        if cache is None:
            cache = ui._scene_fl_dk_ref_cache = {}
        key = str(nc_path)
        if key in cache:
            return cache[key]
        ref = None
        try:
            fldk_nc = self._find_fl_dk_nc(nc_path)
            if fldk_nc is not None:
                from src.core.Engine import EmbeddedRGBEngine
                ref = np.ascontiguousarray(EmbeddedRGBEngine.band_as_image(str(fldk_nc), 'B13', 1100))
                _proj = getattr(ui, 'projection', None)
                if _proj is not None:
                    _c, ref_gt = _proj.extract_crs_from_ads(fldk_nc)
                else:
                    _c, ref_gt = ui.extract_crs_from_ads(fldk_nc)
                if ref is None or ref_gt is None:
                    ref = None, None
                else:
                    ref = (ref, ref_gt)
        except Exception:
            ref = (None, None)
        cache[key] = ref
        return ref


    def _find_target_sidecar(self, nc_path):
        sidecar = nc_path.with_suffix('.ads.json')
        if sidecar.exists():
            return sidecar
        if not nc_path.parent.exists():
            return None
        tm = _re.search(r'_(R\d{3}|JP\d{2})(?:_|\.|$)', nc_path.name)
        token = tm.group(1) if tm else None
        fallback = None
        for f in nc_path.parent.glob('AHI_*.ads.json'):
            if fallback is None:
                fallback = f
            if token is None:
                continue
            if token in f.name:
                return f
        return fallback

    def _target_obs_utc(self, nc_path):
        try:
            sidecar = self._find_target_sidecar(nc_path)
            if sidecar is not None:
                with open(sidecar, 'r', encoding='utf-8') as f:
                    ads = json.load(f)
                    ost = ads.get('observation_start_time')
                    if ost:
                        return datetime.fromisoformat(str(ost).replace('Z', '+00:00')).replace(tzinfo=None)
                    folder_ts = ads.get('timestamp')
                    if folder_ts:
                        try:
                            base_dt = datetime.strptime(folder_ts, '%Y_%m_%d_%H%M')
                            rm = _re.search(r'_R(\d{3})(?:_|\.|$)', nc_path.name)
                            if rm:
                                sub = int(rm.group(1)) % 100
                                return base_dt + timedelta(seconds=(sub - 1) * 150)
                        except (ValueError, TypeError):
                            pass
            fm = _re.search(r'_(\d{4})_(\d{2})_(\d{2})_(\d{4})', str(nc_path.parent.name))
            if fm:
                base_dt = datetime(int(fm.group(1)), int(fm.group(2)), int(fm.group(3)), int(fm.group(4)[:2]), int(fm.group(4)[2:]))
                rm = _re.search(r'_R(\d{3})(?:_|\.|$)', nc_path.name)
                if rm:
                    sub = int(rm.group(1)) % 100
                    return base_dt + timedelta(seconds=(sub - 1) * 150)
            return None
        except Exception:
            pass
        return None

    def _build_band_map_for_hits(self, hits, anim_sat, is_rgb, selector):
        """Build a band<->file map and pick which NC to open, mirroring the
        FLDK discovery logic so target frames honour the same band/RGB content."""
        per_band = [h for h in hits if "_B" in h.name]
        if not per_band and "goes" in anim_sat.lower():
            per_band = [h for h in hits if _re.search(r'C(\d{2})', h.name)]
        bfm = None
        nc = None
        if per_band:
            bfm = {}
            for h in per_band:
                bm = _re.search(r'C(\d{2})', h.name)
                if bm and "goes" in anim_sat.lower():
                    c_num = int(bm.group(1))
                    if c_num == 2:
                        bfm["B03"] = h
                    elif c_num == 3:
                        bfm["B02"] = h
                    else:
                        bfm[f"B{c_num:02d}"] = h
                    bfm[f"C{c_num:02d}"] = h
                else:
                    bm = _re.search(r'_B(\d{2})_', h.name)
                    if bm:
                        bfm[f"B{bm.group(1)}"] = h
        if not is_rgb and "goes" in anim_sat.lower() and bfm and selector in bfm:
            nc = bfm[selector]
        elif is_rgb or not bfm:
            nc = hits[0]
        elif bfm and selector in bfm:
            nc = bfm[selector]
        elif bfm:
            nc = hits[0]
        return (bfm, nc)


    def load_dropped_animation(self, frames, engine_label="himawari9", band=None,
                               autoplay=True):
        """Build and play an animation directly from dropped files.

        Args:
            frames: list of ``(timestamp, nc_path, band_file_map)`` tuples,
                sorted in chronological order.
            engine_label: satellite name used to pick the engine class
                (e.g. ``"GOES 16"`` or ``"himawari9"``).
            band: band to animate; auto-selected from the bands that are
                common to every frame when *None*.
            autoplay: start playback once the prefetch has finished.

        Returns:
            bool: True when the sequence was built and prefetch started.
        """
        try:
            if not frames:
                return False

            self._cancel_anim_prefetch()
            self.main_ui._anim_target_gt_cache = {}
            self.main_ui._anim_composite_cache = {}
            self.main_ui._anim_composite_order = []
            self._composite_cache_bytes = 0
            self.main_ui._anim_sector_tile_cache = {}
            self.main_ui._anim_last_track_cx = None
            self.main_ui._anim_last_track_cy = None

            timestamps = [ts for ts, _nc, _bfm in frames]
            n = len(timestamps)
            self.main_ui._anim_timestamps = timestamps[:]
            self.main_ui._anim_frames = [None] * n
            self.main_ui._anim_band_frames = [None] * n
            self.main_ui._anim_nc_paths = [(nc, bfm) if bfm else nc for (_ts, nc, bfm) in frames]
            self.main_ui._anim_crs_list = [None] * n
            self.main_ui._anim_geotransform_list = [None] * n
            self.main_ui._anim_restored_count = 0
            self.main_ui._anim_index = 0
            self.main_ui._anim_playing = False
            self.main_ui._last_shown_valid = None
            self.main_ui._anim_content_signature = ("drop", tuple(timestamps))
            self.main_ui._anim_base_signature = ("drop", tuple(timestamps))
            self.main_ui._anim_geo_mode = False
            self.main_ui._anim_target_frames = [{} for _ in range(n)]
            self.main_ui._anim_target_crs = [{} for _ in range(n)]
            self.main_ui._anim_target_gt = [{} for _ in range(n)]
            self.main_ui._anim_target_nc = [[] for _ in range(n)]
            self.main_ui._anim_target_band_frames = [{} for _ in range(n)]
            self.main_ui._anim_target_item_map = {}
            self.main_ui._anim_target_base = None
            self.main_ui._anim_fldk_item_to_masters = None
            self.main_ui._anim_sync_product_idx = None
            self.main_ui._anim_sync_target_meta = None
            self.main_ui._anim_sync_pending_idx = None
            self._scene_prod_sync_sig = None
            self._scene_prod_sync_at = 0.0
            self._scene_prod_sync_prod = None
            if hasattr(self.main_ui, 'anim_play_btn'):
                self.main_ui.anim_play_btn.setText("\u25B6 Play")

            for i, (_ts, nc, _bfm) in enumerate(frames):
                try:
                    _c, _g = self.main_ui.extract_crs_from_ads(nc)
                    self.main_ui._anim_crs_list[i] = _c
                    self.main_ui._anim_geotransform_list[i] = _g
                except Exception:
                    pass
            self._setup_anim_registration()

            if band is None:
                band = self._pick_drop_band(frames)
            if not band:
                self.main_ui.log("Drop animation cancelled: no band is common to every dropped frame.")
                return False
            if hasattr(self.main_ui, '_anim_selected_band'):
                self.main_ui._anim_selected_band = band
            if hasattr(self.main_ui, '_anim_checked_bands'):
                self.main_ui._anim_checked_bands = [band]
            if hasattr(self.main_ui, '_anim_active_content'):
                self.main_ui._anim_active_content = ("BAND", band)
            if hasattr(self.main_ui, '_set_anim_content_filter'):
                try:
                    self.main_ui._set_anim_content_filter("Band")
                except Exception:
                    pass

            if hasattr(self.main_ui, 'anim_frame_slider'):
                self.main_ui.anim_frame_slider.setRange(0, max(0, n - 1))
                self.main_ui.anim_frame_slider.setValue(0)
            if hasattr(self.main_ui, 'anim_frame_label'):
                self.main_ui.anim_frame_label.setText(f"Frame: 1 / {n}")
            if hasattr(self.main_ui, 'anim_ready_label'):
                self.main_ui.anim_ready_label.setText(f"Ready: 0 / {n} frames")

            max_px = getattr(self.main_ui, 'preview_max_px', 2200)
            load_items = []
            ac = ("BAND", band)
            for i, (_ts, nc, bfm) in enumerate(frames):
                load_items.append((i, nc, (band,), ac, max_px, bfm or None))

            if hasattr(self.main_ui, 'anim_progress'):
                self.main_ui.anim_progress.setVisible(True)
                self.main_ui.anim_progress.setMaximum(n)
                self.main_ui.anim_progress.setValue(0)
            if hasattr(self.main_ui, 'anim_target_progress'):
                self.main_ui.anim_target_progress.setVisible(False)
            if hasattr(self.main_ui, 'anim_japan_progress'):
                self.main_ui.anim_japan_progress.setVisible(False)

            _eng = get_engine(engine_label)
            self.main_ui._anim_drop_autoplay = autoplay
            self.main_ui._anim_prefetch_worker = AnimationPrefetchWorker(
                load_items, engine_class=_eng)
            self.main_ui._anim_prefetch_thread = QThread()
            worker = self.main_ui._anim_prefetch_worker
            thread = self.main_ui._anim_prefetch_thread
            worker.moveToThread(thread)
            worker.frame_ready.connect(self._on_anim_frame_ready)
            worker.band_frame_ready.connect(self._on_anim_band_frame_ready)
            worker.progress.connect(self._on_anim_prefetch_progress)
            worker.finished.connect(self._on_anim_prefetch_finished)
            thread.started.connect(worker.run)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            self.main_ui._anim_just_started = True
            thread.start()
            self.main_ui.log(
                f"Drop animation: {n} frame(s) loaded as a sequence (band {band}, engine {engine_label}). Playing when ready."
            )
            return True
        except Exception as e:
            import traceback
            self.main_ui.log(f"Drop animation error: {e}")
            traceback.print_exc()
            return False

    def _pick_drop_band(self, frames):
        """Pick the band present in every dropped frame (B13 when available).

        Frames that do not carry a band<->file map are skipped for the common
        set (their content is unknown). If no single band is common, fall back
        to a well-known preference, then to the first band any frame declares,
        then to the sidecar band list, so a drop of per-band NC files still
        animates instead of being aborted.
        """
        mapped = [bfm for _ts, _nc, bfm in frames if bfm]
        common = None
        for bfm in mapped:
            bands = set(bfm.keys())
            if common is None:
                common = bands
            else:
                common &= bands
            if not common:
                break
        if not common:
            # Last resort: intersect the bands declared in each NC's sidecar.
            for _ts, nc, _bfm in frames:
                if nc is None:
                    continue
                try:
                    side = Path(nc).with_suffix('.ads.json')
                except Exception:
                    continue
                if not side.exists():
                    continue
                try:
                    with open(side, 'r', encoding='utf-8') as _fh:
                        stats = json.load(_fh).get('band_stats', {}) or {}
                    keys = set(k for k in stats.keys() if isinstance(k, str))
                except Exception:
                    keys = set()
                if common is None:
                    common = keys
                else:
                    common &= keys
                if not common:
                    break
        if not common:
            return None
        for preferred in ("B13", "B01", "B03"):
            if preferred in common:
                return preferred
        return sorted(common)[0]


    def _on_anim_content_changed(self, text: str):
        pass


    def _is_geo_target_text(self, text: str) -> bool:
        return (text or "").strip().lower() in ("geo target", "geotarget", "geo",
                                                 "full geo", "fullgeo", "fullgeotarget")


    def _kind_for_sector(self, sector: str) -> str:
        """Classify a target-area sector into a progress-bar bucket.
        Japan regional tiles (Himawari 'Japan:JPxx') and GOES CONUS ('C')
        share the 'japan' bar; actual target/meso sectors use 'target'."""
        s = str(sector).lower()
        return 'japan' if (s.startswith('japan') or s == 'c') else 'target'


    def _anim_kind_hint(self) -> str:
        """Bucket hint for the master (non-geo) frames: matches the animation
        type, since a raw Target/Japan/Full Disk load is single-kind.
        GOES CONUS plays the Japan/regional role; Meso plays the Target role."""
        try:
            data = str(self.main_ui.anim_type_combo.currentData() or "").strip().lower()
        except Exception:
            data = ""
        if data in ('japan', 'conus'):
            return 'japan'
        if data in ('target', 'meso'):
            return 'target'
        return 'fd'


    def _geotarget_uses_rapid(self) -> bool:
        sat = self.main_ui.anim_sat_combo.currentText().lower() if hasattr(self.main_ui, 'anim_sat_combo') else ""
        return "himawari" in sat


    def _get_anim_geo_sectors(self):
        type_data = ""
        if hasattr(self.main_ui, 'anim_type_combo'):
            type_data = self.main_ui.anim_type_combo.currentData() or ""
        type_data = str(type_data).strip().lower()
        is_fullgeo = type_data == "fullgeo"
        if not hasattr(self.main_ui, '_anim_geo_sector_combo'):
            return self._default_geo_sectors()
        sat = self.main_ui.anim_sat_combo.currentText().lower() if hasattr(self.main_ui, 'anim_sat_combo') else ""
        sel = self.main_ui._anim_geo_sector_combo.currentData()
        if "himawari" in sat:
            if is_fullgeo:
                return {"All": ["Japan", "Target"], "Target": ["Target"],
                        "Japan": ["Japan"]}.get(sel, ["Japan", "Target"])
            return ["Target"]
        if is_fullgeo:
            return {"All": ["C", "M1", "M2"], "C": ["C"], "Both": ["M1", "M2"],
                    "M1": ["M1"], "M2": ["M2"]}.get(sel, ["C", "M1", "M2"])
        return {"Both": ["M1", "M2"], "M1": ["M1"],
                "M2": ["M2"]}.get(sel, ["M1", "M2"])


    def _get_anim_geo_style(self):
        if hasattr(self.main_ui, '_anim_geo_style_combo'):
            cur = self.main_ui._anim_geo_style_combo.currentData()
            if cur in ("blend", "border"):
                return cur
        return "border"


    def _anim_composite_sig(self, display_idx):
        """Signature of everything that affects the composited frame for a slot."""
        ui = self.main_ui
        try:
            ctype, csel = ui._get_anim_active_content()
        except Exception:
            ctype, csel = "BAND", ""
        checked = ()
        try:
            checked = tuple(sorted(ui._get_anim_checked_bands()))
        except Exception:
            checked = ()
        try:
            prod = getattr(ui, 'selected_product', None)
        except Exception:
            prod = None
        geo_mode = getattr(ui, '_anim_geo_mode', False)
        geo_sig = ""
        if geo_mode:
            try:
                geo_sig = (str(ui._anim_geo_sector_combo.currentData()),
                           str(ui.anim_type_combo.currentText()),
                           self._get_anim_geo_style() or 'border')
            except Exception:
                geo_sig = ""
        has_targets = False
        try:
            has_targets = bool((ui._anim_target_frames or [])[display_idx])
        except Exception:
            has_targets = False
        style = None
        if geo_mode:
            style = self._get_anim_geo_style() or 'border'
        border = None
        try:
            border = ui.settings.get('geotarget_border_color', None)
        except Exception:
            border = None
        sat = ui.anim_sat_combo.currentText() if hasattr(ui, 'anim_sat_combo') else ""
        return (prod, ctype, csel, checked, geo_mode, geo_sig, has_targets, style, border, sat)


    def _store_composite(self, display_idx, sig, arr):
        """Store a composited RGBA frame under a memory-bounded LRU."""
        ui = self.main_ui
        if arr is None:
            return
        cache = getattr(ui, '_anim_composite_cache', None)
        if cache is None:
            cache = {}
            ui._anim_composite_cache = cache
            ui._anim_composite_order = []
        order = ui._anim_composite_order
        if not hasattr(self, '_composite_cache_bytes'):
            self._composite_cache_bytes = 0
        budget = getattr(self, '_composite_cache_budget', 400 * 1024 * 1024)
        arr_bytes = arr.dtype.itemsize * arr.size
        prev = cache.get(display_idx)
        if prev is not None:
            # Replacing an existing entry: release its accounted bytes first.
            self._composite_cache_bytes -= prev[1].dtype.itemsize * prev[1].size
            if id(prev[1]) == id(arr):
                # Reuse arrays already identical in the cache (avoids dup memory).
                cache[display_idx] = (sig, arr)
                self._composite_cache_bytes += arr_bytes
                if display_idx in order:
                    order.remove(display_idx)
                order.append(display_idx)
                return
        while cache and self._composite_cache_bytes + arr_bytes > budget:
            old_k = order.pop(0) if order else next(iter(cache.keys()))
            old = cache.pop(old_k, None)
            if old is not None:
                self._composite_cache_bytes -= old[1].dtype.itemsize * old[1].size
        if self._composite_cache_bytes + arr_bytes > budget:
            return
        cache[display_idx] = (sig, arr)
        self._composite_cache_bytes += arr_bytes
        if display_idx in order:
            order.remove(display_idx)
        order.append(display_idx)


    def _default_geo_sectors(self):
        sat = self.main_ui.anim_sat_combo.currentText().lower() if hasattr(self.main_ui, 'anim_sat_combo') else ""
        if "himawari" in sat:
            return ["Japan", "Target"]
        return ["C", "M1", "M2"]


    def _on_anim_type_changed(self, text: str):
        if hasattr(self.main_ui, '_anim_composite_cache'):
            self.main_ui._anim_composite_cache.clear()
        if hasattr(self.main_ui, '_anim_composite_order'):
            self.main_ui._anim_composite_order = []
        self._composite_cache_bytes = 0
        if hasattr(self.main_ui, 'anim_track_cb'):
            is_target = text == "Target"
            is_full_disk = text == "Full Disk"
            is_geo = self._is_geo_target_text(text)
            self.main_ui.anim_track_cb.setEnabled(is_target or is_full_disk or is_geo)
            if not is_target and not is_full_disk and not is_geo:
                self.main_ui.anim_track_cb.setChecked(False)
        is_geo = self._is_geo_target_text(text)
        is_rapid = text.lower() in ("japan", "target") or (is_geo and self._geotarget_uses_rapid())
        if is_geo and hasattr(self.main_ui, '_set_anim_geo_controls_visible'):
            try:
                self.main_ui._set_anim_geo_controls_visible(True)
            except Exception:
                pass
        elif hasattr(self.main_ui, '_set_anim_geo_controls_visible'):
            try:
                self.main_ui._set_anim_geo_controls_visible(False)
            except Exception:
                pass
        if is_rapid:
            mins = ["00", "02", "05", "07", "10", "12", "15", "17",
                    "20", "22", "25", "27", "30", "32", "35", "37",
                    "40", "42", "45", "47", "50", "52", "55", "57"]
        else:
            mins = [f"{m:02d}" for m in range(0, 60, 10)]
        for attr in ['anim_from_minute', 'anim_to_minute']:
            if hasattr(self.main_ui, attr):
                cb = getattr(self.main_ui, attr)
                if cb:
                    cb.blockSignals(True)
                    current = cb.currentText()
                    cb.clear()
                    cb.addItems(mins)
                    cb.setCurrentText(current if current in mins else mins[0])
                    cb.blockSignals(False)
        if hasattr(self.main_ui, 'anim_time_step'):
            ts = self.main_ui.anim_time_step
            ts.blockSignals(True)
            if is_rapid:
                if ts.findText("2.5 min") < 0:
                    ts.insertItem(0, "2.5 min")
                ts.setCurrentText("2.5 min")
            else:
                idx = ts.findText("2.5 min")
                if idx >= 0:
                    ts.removeItem(idx)
                if ts.currentText() == "2.5 min" or ts.currentText() not in [ts.itemText(i) for i in range(ts.count())]:
                    ts.setCurrentText("10 min")
            ts.blockSignals(False)


    def _get_target_gt_for_ts(self, timestamp):
        cache = getattr(self.main_ui, '_anim_target_gt_cache', {})
        if timestamp in cache:
            _c0 = cache[timestamp]
            if isinstance(_c0, tuple) and len(_c0) == 3:
                return (_c0[0], _c0[1], _c0[2], None)
            return _c0
        ui = self.main_ui
        if hasattr(ui, 'anim_sat_combo') and ui.anim_sat_combo.currentData():
            anim_sat = ui.anim_sat_combo.currentData()
        elif hasattr(ui, 'anim_sat_combo'):
            anim_sat = ui.anim_sat_combo.currentText()
        else:
            anim_sat = 'himawari9'
        base = Path(ui.input_dir) / anim_sat
        result = (None, None, None)
        if base.exists():
            ts_variants = [timestamp]
            parts = timestamp.rsplit('_', 1)
            if len(parts) == 2:
                compact_ts = parts[0].replace('_', '') + '_' + parts[1]
                if compact_ts != timestamp:
                    ts_variants.append(compact_ts)
            for tsv in ts_variants:
                matches = sorted(base.glob(f'*{tsv}*'), key=lambda p: p.name)
                matches = [m for m in matches if 'AHI-L1b-Target' in m.name]
                for item in matches:
                    nc_candidates = []
                    if item.is_dir():
                        nc_candidates = sorted(item.glob('*.nc'), key=lambda p: p.name)
                    elif item.suffix == '.nc':
                        nc_candidates = [item]
                    for nc_path in nc_candidates:
                        _c, _g = ui.extract_crs_from_ads(nc_path)
                        if not _c or not _g:
                            continue
                        shape = None
                        sidecar = nc_path.with_suffix('.ads.json')
                        if not sidecar.exists():
                            import re as _re
                            _mt = _re.search(r'_(R\d{3}|JP\d{2})_', nc_path.name)
                            _area = _mt.group(1) if _mt else None
                            _sfx = '_' + _area if _area else ''
                            sidecar = nc_path.parent / f'AHI_Target_{timestamp}{_sfx}.ads.json'
                        if sidecar.exists():
                            try:
                                with open(sidecar, 'r') as f:
                                    ads_data = json.load(f)
                                bstats = ads_data.get('band_stats', {})
                                for bname, binfo in bstats.items():
                                    s = binfo.get('shape')
                                    if s and len(s) == 2:
                                        shape = s
                                        break
                            except Exception:
                                pass
                        if shape is None:
                            try:
                                import xarray as xr
                                with xr.open_dataset(nc_path, engine='netcdf4') as ds:
                                    for vname in list(ds.data_vars):
                                        s = ds[vname].shape
                                        if len(s) == 2:
                                            shape = list(s)
                                            break
                            except Exception:
                                pass
                        if shape is None:
                            shape = [550, 550]
                        result = (_c, _g, shape, nc_path)
                        break
                    if result[0] is None:
                        continue
                    break
                if result[0] is None:
                    continue
                break
        cache[timestamp] = result
        return result

    def _update_anim_band_labels(self):
        sat = self.main_ui.anim_sat_combo.currentText().lower()
        is_goes = 'goes' in sat
        prefix = 'C' if is_goes else 'B'

        def _map_band(name):
            if name and len(name) == 3 and name[0] in ('B', 'C') and name[1:].isdigit():
                return f"{prefix}{name[1:]}"
            return f"{prefix}13"

        current = self.main_ui._anim_selected_band
        mapped = _map_band(current)
        self.main_ui._anim_selected_band = mapped
        checked = []
        for name in self.main_ui._anim_checked_bands:
            m = _map_band(name)
            if m not in checked:
                checked.append(m)
        if not checked:
            checked = [mapped]
        self.main_ui._anim_checked_bands = checked
        self.main_ui._anim_active_content = ('BAND', mapped)
        if hasattr(self.main_ui, '_build_anim_content_grid'):
            try:
                self.main_ui._build_anim_content_grid()
            except Exception:
                pass
        for i in range(1, 17):
            new_label = f"{prefix}{i:02d}"
            cb = self.main_ui._anim_band_checkboxes.get(new_label)
            if cb is None:
                continue
            try:
                cb.setText(new_label)
                cb.setChecked(new_label in checked)
            except Exception:
                pass
        if hasattr(self.main_ui, '_update_anim_checked_bands_label'):
            self.main_ui._update_anim_checked_bands_label()

    def _update_anim_type_options(self, *args):
        if not hasattr(self.main_ui, 'anim_type_combo') or not hasattr(self.main_ui, 'anim_sat_combo'):
            return
        sat = self.main_ui.anim_sat_combo.currentText().lower()
        current = self.main_ui.anim_type_combo.currentData() if hasattr(self.main_ui, 'anim_type_combo') else "FLDK"

        self.main_ui.anim_type_combo.blockSignals(True)
        self.main_ui.anim_type_combo.clear()

        if "himawari" in sat:
            self.main_ui.anim_type_combo.addItem("Full Disk", "FLDK")
            self.main_ui.anim_type_combo.addItem("Japan", "Japan")
            self.main_ui.anim_type_combo.addItem("Target", "Target")
            self.main_ui.anim_type_combo.addItem("Geo Target", "Geo")
            self.main_ui.anim_type_combo.addItem("Full GEO", "FullGEO")
            if current in ["FLDK", "Japan", "Target", "Geo", "FullGEO"]:
                idx = self.main_ui.anim_type_combo.findData(current)
                if idx >= 0:
                    self.main_ui.anim_type_combo.setCurrentIndex(idx)
            else:
                idx = self.main_ui.anim_type_combo.findData("FLDK")
                if idx >= 0:
                    self.main_ui.anim_type_combo.setCurrentIndex(idx)
        elif "gk2a" in sat or "gk-2a" in sat:
            self.main_ui.anim_type_combo.addItem("Full Disk", "Full")
            idx = self.main_ui.anim_type_combo.findData("Full")
            if idx >= 0:
                self.main_ui.anim_type_combo.setCurrentIndex(idx)
        elif "meteosat" in sat or "msg" in sat:
            self.main_ui.anim_type_combo.addItem("Full Disk", "Full")
            idx = self.main_ui.anim_type_combo.findData("Full")
            if idx >= 0:
                self.main_ui.anim_type_combo.setCurrentIndex(idx)
        else:
            self.main_ui.anim_type_combo.addItem("Full Disk", "Full")
            self.main_ui.anim_type_combo.addItem("CONUS", "CONUS")
            self.main_ui.anim_type_combo.addItem("Meso", "Meso")
            self.main_ui.anim_type_combo.addItem("Geo Target", "Geo")
            self.main_ui.anim_type_combo.addItem("Full GEO", "FullGEO")
            idx = self.main_ui.anim_type_combo.findData("Full")
            if idx >= 0:
                self.main_ui.anim_type_combo.setCurrentIndex(idx)

        self.main_ui.anim_type_combo.blockSignals(False)
        self._on_anim_type_changed(self.main_ui.anim_type_combo.currentText())


    def get_anim_himawari_product(self) -> str:
        if not hasattr(self.main_ui, 'anim_sat_combo') or not hasattr(self.main_ui, 'anim_type_combo'):
            return "AHI-L1b-FLDK"
        sat = self.main_ui.anim_sat_combo.currentText().lower()
        sid = (self.main_ui.anim_sat_combo.currentData() or sat).lower()
        if "himawari" in sat:
            typ = self.main_ui.anim_type_combo.currentData() or self.main_ui.anim_type_combo.currentText()
            mapping = {
                "FLDK": "AHI-L1b-FLDK",
                "Japan": "AHI-L1b-Japan",
                "Target": "AHI-L1b-Target",
                "Geo": "AHI-L1b-FLDK",
            }
            if self._is_geo_target_text(typ) or self._is_geo_target_text(self.main_ui.anim_type_combo.currentText()):
                return "AHI-L1b-FLDK"
            return mapping.get(typ, "AHI-L1b-FLDK")
        if "meteosat" in sid or "msg" in sid:
            return "MSG-L1B-FD"
        if "gk2a" in sid or "gk-2a" in sid:
            return "GK2A-L1B-FD"
        return "ABI-L1b-RadF"
