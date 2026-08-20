# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: managers/alert_manager.py
# Description: Alert management system for notification handling and alert state persistence.
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
import logging
import subprocess as _subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from ..clients.pagasa_alerts import (
    PAGASAAlertFetcher,
)
from ..clients.weathergov import (
    WeatherGovAlertFetcher,
    is_emergency,
    severity_score,
)

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
_CREATE_NO_WINDOW = getattr(_subprocess, "CREATE_NO_WINDOW", 0)

SOUNDS_DIR = Path(__file__).resolve().parent.parent.parent / "public" / "sounds"
SOUND_NOTIF = SOUNDS_DIR / "notif.m4a"
SOUND_MODERN_NOTIF = SOUNDS_DIR / "modern_notif.m4a"
SOUND_EMER = SOUNDS_DIR / "Emer.m4a"
SOUND_SOFT_EMER = SOUNDS_DIR / "Soft_Emer.m4a"
SOUND_FULEMER = SOUNDS_DIR / "Fulemer.m4a"

_SEEN_FILE = Path(__file__).resolve().parent.parent.parent / "cache" / "seen_alerts.json"
_ACK_FILE = Path(__file__).resolve().parent.parent.parent / "cache" / "acknowledged_alerts.json"


def is_expired(alert):
    """Check if an alert has passed its expires time."""
    expires = alert.get("expires", "")
    if not expires:
        return False
    try:
        exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        return exp_dt < datetime.now(timezone.utc)
    except Exception:
        return False


_current_player = None
_current_audio_output = None
_current_process = None
_current_tts_process = None
_loop_timer = None
_loop_stop_timer = None


def is_full_emergency(alert):
    """Full emergency threshold: only PAGASA color-coded RED warnings
    (rainfall/flood) trigger the full-emergency siren (Fulemer.m4a).
    """
    return alert.get("color_level", "") == "RED"


def is_minor(alert):
    return alert.get("severity", "") == "Minor"


def is_pagasa_extreme_flood(alert):
    """PAGASA color-coded RED rainfall or flood warnings use the
    full-emergency siren (Fulemer.m4a). Subtype-based Extreme advisories
    (e.g. General Flood Advisory (Extreme)) do NOT trigger Fulemer; they
    are handled as a regular emergency (Emer.m4a).
    """
    return alert.get("color_level", "") == "RED"


def stop_sounds():
    global _current_player, _current_audio_output, _loop_timer, _loop_stop_timer, _current_process, _current_tts_process
    if _loop_timer is not None:
        try:
            _loop_timer.stop()
        except Exception:
            pass
        _loop_timer = None
    if _loop_stop_timer is not None:
        try:
            _loop_stop_timer.stop()
        except Exception:
            pass
        _loop_stop_timer = None
    if _current_process is not None:
        try:
            _current_process.terminate()
        except Exception:
            pass
        try:
            _current_process.kill()
        except Exception:
            pass
        _current_process = None
    if _current_player is not None:
        try:
            _current_player.stop()
        except Exception:
            pass
        _current_player = None
    if _current_tts_process is not None:
        try:
            _current_tts_process.terminate()
        except Exception:
            pass
        try:
            _current_tts_process.kill()
        except Exception:
            pass
        _current_tts_process = None
    _current_audio_output = None


def stop_tts():
    """Stop only the TTS (text-to-speech) process, leave alert sounds alone."""
    global _current_tts_process
    if _current_tts_process is not None:
        try:
            _current_tts_process.terminate()
        except Exception:
            pass
        try:
            _current_tts_process.kill()
        except Exception:
            pass
        _current_tts_process = None


def _play_m4a_impl(path, loop=False):
    """Play an m4a file once. Does not stop previous playback.
    If loop=True, uses ffplay's built-in looping (single process)."""
    global _current_player, _current_audio_output, _current_process
    if not path.exists():
        log.warning(f"Sound file not found: {path}")
        return False
    resolved = str(path.resolve())
    log.info(f"Attempting to play: {resolved}")

    # Method 1: ffplay (ships with PySide6 FFmpeg, no window)
    import shutil
    ffplay = shutil.which("ffplay")
    if ffplay:
        try:
            import subprocess
            ffplay_args = [ffplay, '-nodisp', '-volume', '80']
            if loop:
                ffplay_args += ['-loop', '0']
            else:
                ffplay_args += ['-autoexit']
            ffplay_args.append(resolved)
            proc = subprocess.Popen(
                ffplay_args,
                creationflags=_CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _current_process = proc
            log.info("Playing via ffplay")
            return True
        except Exception as e:
            log.warning(f"ffplay failed: {e}")

    # Method 2: PowerShell WMP COM (Windows only)
    if IS_WINDOWS:
        try:
            import subprocess
            safe_path = resolved.replace("'", "''")
            play_count = "0" if loop else "1"
            proc = subprocess.Popen(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command',
                 f"$wm = New-Object -ComObject WMPlayer.OCX; $wm.settings.volume = 80; $wm.settings.playCount = {play_count}; $wm.URL = '{safe_path}'; $wm.controls.play(); Start-Sleep 3; $wm.close()"],
                creationflags=_CREATE_NO_WINDOW,
            )
            _current_process = proc
            log.info("Playing via WMP COM")
            return True
        except Exception as e:
            log.warning(f"WMP COM failed: {e}")

    # Method 3: QMediaPlayer
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        player = QMediaPlayer()
        audio_output = QAudioOutput()
        player.setAudioOutput(audio_output)
        player.setSource(QUrl.fromLocalFile(resolved))
        audio_output.setVolume(0.8)
        player.play()
        _current_player = player
        _current_audio_output = audio_output
        _current_process = None
        log.info("Playing via QMediaPlayer")
        if loop:
            def replay_loop(status):
                if status == QMediaPlayer.EndOfMedia:
                    player.play()
            player.mediaStatusChanged.connect(replay_loop)
        return True
    except Exception as e:
        log.error(f"All playback methods failed: {e}")
        return False


def _play_m4a(path):
    """Play an m4a file once. Stops any previous playback first."""
    stop_sounds()
    return _play_m4a_impl(path)


def _play_m4a_looped(path, duration_sec):
    """Play an m4a file on repeat for `duration_sec` seconds.
    Uses ffplay built-in looping (single process, no stacking)."""
    global _loop_timer, _loop_stop_timer
    stop_sounds()
    _play_m4a_impl(path, loop=True)

    _loop_stop_timer = QTimer()
    _loop_stop_timer.setSingleShot(True)
    _loop_stop_timer.timeout.connect(stop_sounds)
    _loop_stop_timer.start(duration_sec * 1000)


def play_notification(use_modern=False):
    _play_m4a(SOUND_MODERN_NOTIF if use_modern else SOUND_NOTIF)


def play_emergency(use_soft=False):
    _play_m4a_looped(SOUND_SOFT_EMER if use_soft else SOUND_EMER, 300)


def play_full_emergency():
    _play_m4a_looped(SOUND_FULEMER, 600)


def speak_alert(alert, mode="brief"):
    """Speak the alert out loud (SAPI TTS on Windows, say/espeak/Qt elsewhere).

    Args:
        alert: The alert dict
        mode: "brief" for automatic alerts (event name only), "full" for manual button (all details)
    """
    try:
        import subprocess
        event = alert.get("event", "Alert")
        # Include event code only when it adds info not already in the event name
        event_code = alert.get("event_code", {})
        source = alert.get("source", "")
        if source == "PAGASA":
            # PAGASA event already contains subtype, skip redundant raw code
            pass
        elif event_code:
            for _k, v in event_code.items():
                if v:
                    event = f"{event} ({v})"
                    break

        if mode == "brief":
            # Brief: just the alert name
            text = f"{event}."
        else:
            # Full details for manual button
            headline = alert.get("headline", "")
            description = alert.get("description", "")
            instruction = alert.get("instruction", "")
            severity = alert.get("severity", "")
            urgency = alert.get("urgency", "")
            certainty = alert.get("certainty", "")
            effective = alert.get("effective", "")
            expires = alert.get("expires", "")
            sender = alert.get("sender", "")
            areas = ", ".join(alert.get("affected_zones", []))
            if not areas:
                areas = sender if sender else "your area"

            parts = [
                f"{event}.",
                f"Severity: {severity} | Urgency: {urgency} | Certainty: {certainty}.",
                f"{headline}.",
                f"{description}.",
                f"Instructions: {instruction}.",
                f"Effective: {effective[:19] if effective else 'Unknown'} | Expires: {expires[:19] if expires else 'Unknown'}.",
                f"Sender: {sender}.",
                f"Affected areas: {areas}.",
            ]
            text = " ".join(parts)
        if IS_WINDOWS:
            # Escape for PowerShell
            safe_text = text.replace("'", "''").replace('"', '""')
            subprocess.Popen(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command',
                 f"Add-Type -AssemblyName System.Speech; $speak = New-Object System.Speech.Synthesis.SpeechSynthesizer; $speak.Rate = 0; $speak.Volume = 80; $speak.Speak('{safe_text}')"],
                creationflags=_CREATE_NO_WINDOW,
            )
        else:
            _speak_tts_cross_platform(text)
    except Exception as e:
        log.warning(f"TTS failed: {e}")


def _speak_tts_cross_platform(text):
    """Best-effort TTS on macOS/Linux via `say`/`espeak`, falling back to Qt."""
    import shutil

    try:
        if sys.platform == "darwin":
            say = shutil.which("say")
            if say:
                _subprocess.Popen(
                    [say, "-r", "170", text],
                    stdout=_subprocess.DEVNULL,
                    stderr=_subprocess.DEVNULL,
                )
                return
        espeak = shutil.which("espeak") or shutil.which("espeak-ng")
        if espeak:
            _subprocess.Popen(
                [espeak, "-s", "170", text],
                stdout=_subprocess.DEVNULL,
                stderr=_subprocess.DEVNULL,
            )
            return
    except Exception as e:
        log.warning(f"CLI TTS failed: {e}")

    try:
        from PySide6.QtTextToSpeech import QTextToSpeech
        tts = QTextToSpeech()
        tts.say(text)
    except Exception as e:
        log.warning(f"Qt TTS failed: {e}")


def _load_seen_ids():
    try:
        if _SEEN_FILE.exists():
            with open(_SEEN_FILE) as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()


def _save_seen_ids(ids):
    try:
        _SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SEEN_FILE, "w") as f:
            json.dump(sorted(ids), f)
    except Exception:
        pass


def _load_ack_ids():
    try:
        if _ACK_FILE.exists():
            with open(_ACK_FILE) as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()


def _save_ack_ids(ids):
    try:
        _ACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_ACK_FILE, "w") as f:
            json.dump(sorted(ids), f)
    except Exception:
        pass


class SubprocessAlertFetcher(QObject):
    """Fetches alerts by launching Process/alert/alert_fetcher.py as a subprocess.

    Runs the standalone alert fetcher in an external OS process for isolation.
    Communicates results via JSON over stdout.

    Signals
    -------
    alerts_fetched : list[dict]
        Parsed alert dicts from all requested sources.
    error : str
        Error message on failure.
    """

    alerts_fetched = Signal(list)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def configure(self, lat=None, lon=None, zone=None, marine=False, sources=None):
        self._lat = lat
        self._lon = lon
        self._zone = zone
        self._marine = marine
        self._sources = sources if sources is not None else ["NWS", "PAGASA"]

    def run(self):
        import sys as _sys

        self._cancelled = False

        fetcher_path = Path(__file__).resolve().parent.parent.parent / "Process" / "alert" / "alert_fetcher.py"
        if not fetcher_path.exists():
            self.error.emit(f"alert_fetcher.py not found at {fetcher_path}")
            return

        cmd = [_sys.executable, str(fetcher_path)]
        cmd.append("--sources")
        cmd.append(",".join(self._sources))
        if self._lat is not None and self._lon is not None:
            cmd.extend(["--lat", str(self._lat), "--lon", str(self._lon)])
        if self._zone:
            cmd.extend(["--zone", self._zone])
        if self._marine:
            cmd.append("--marine")

        try:
            proc = _subprocess.Popen(
                cmd,
                stdout=_subprocess.PIPE,
                stderr=_subprocess.PIPE,
                text=True,
                creationflags=_CREATE_NO_WINDOW,
            )
            stdout_data, stderr_data = proc.communicate(timeout=120)

            if self._cancelled:
                return

            if stderr_data.strip():
                for line in stderr_data.strip().splitlines():
                    log.debug(f"[alert_subprocess] {line}")

            if proc.returncode != 0:
                if stderr_data.strip():
                    self.error.emit(f"Subprocess error (code {proc.returncode}): {stderr_data.strip()}")
                else:
                    self.error.emit(f"Subprocess exited with code {proc.returncode}")
                return

            if not stdout_data or not stdout_data.strip():
                self.alerts_fetched.emit([])
                return

            alerts = json.loads(stdout_data)
            self.alerts_fetched.emit(alerts)

        except _subprocess.TimeoutExpired:
            proc.kill()
            self.error.emit("Alert subprocess timed out after 120s")
        except Exception as e:
            self.error.emit(f"Alert subprocess error: {e}")


class AlertManager(QObject):
    """Manages weather alert polling, deduplication, and multi-tier sound notifications.

    Signals
    -------
    new_alert : dict
        Emitted for each new alert (never seen before).
    emergency_alert : dict
        Emitted for Extreme/Severe + Immediate/Expected alerts.
    fulemer_alert : dict
        Emitted for Extreme + Immediate alerts (tornado-level) and PAGASA Extreme flood.
    alert_summary : str
        Human-readable summary for status bar display.
    poll_error : str
        On API error.
    """

    new_alert = Signal(dict)
    emergency_alert = Signal(dict)
    fulemer_alert = Signal(dict)
    alert_summary = Signal(str)
    poll_error = Signal(str)

    def __init__(self, parent=None, settings=None, use_subprocess=False):
        super().__init__(parent)
        self.settings = settings
        self._use_subprocess = use_subprocess
        self._seen_ids = _load_seen_ids()
        self._nws_fetcher = None
        self._nws_fetcher_thread = None
        self._pagasa_fetcher = None
        self._pagasa_fetcher_thread = None
        self._subprocess_fetcher = None
        self._subprocess_fetcher_thread = None
        self._last_summary = ""
        self._last_nws_alerts = []
        self._last_pagasa_alerts = []

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_interval_ms = 600000

        self._enabled = False
        self._marine_only = False
        self._zone = ""
        self._lat = None
        self._lon = None
        self._use_modern_notif = False
        self._use_soft_emer = False
        self._alert_sources = ["NWS", "PAGASA"]  # Track which sources are enabled

    def start(self, interval_minutes=10):
        self._poll_interval_ms = max(60000, interval_minutes * 60000)
        self._enabled = True
        self._poll()
        self._poll_timer.start(self._poll_interval_ms)
        log.info(f"Alert polling started (every {interval_minutes} min)")

    def stop(self):
        self._enabled = False
        self._poll_timer.stop()
        if self._nws_fetcher:
            self._nws_fetcher.cancel()
        if self._pagasa_fetcher:
            self._pagasa_fetcher.cancel()
        if self._subprocess_fetcher:
            self._subprocess_fetcher.cancel()
            self._subprocess_fetcher = None
        log.info("Alert polling stopped")

    def poll_now(self):
        """Trigger an immediate poll regardless of enabled state."""
        self._poll()

    def configure(self, marine_only=False, zone="", lat=None, lon=None,
                  use_modern_notif=False, use_soft_emer=False,
                  alert_sources=None, use_subprocess=None):
        self._marine_only = marine_only
        self._zone = zone
        self._lat = lat
        self._lon = lon
        self._use_modern_notif = use_modern_notif
        self._use_soft_emer = use_soft_emer
        if use_subprocess is not None:
            self._use_subprocess = use_subprocess
        if alert_sources is not None:
            self._alert_sources = alert_sources

    def _poll(self):
        if self._use_subprocess:
            self._poll_subprocess()
        else:
            self._poll_qthread()

    def _poll_qthread(self):
        if self._nws_fetcher is not None or self._pagasa_fetcher is not None:
            return

        from PySide6.QtCore import QThread

        if "NWS" in self._alert_sources:
            self._nws_fetcher = WeatherGovAlertFetcher(
                lat=self._lat, lon=self._lon,
                zone=self._zone, marine=self._marine_only,
            )
            self._nws_fetcher.alerts_fetched.connect(self._on_nws_alerts)
            self._nws_fetcher.error.connect(self._on_error)

            self._nws_fetcher_thread = QThread(self)
            self._nws_fetcher.moveToThread(self._nws_fetcher_thread)
            self._nws_fetcher_thread.started.connect(self._nws_fetcher.run)
            self._nws_fetcher.alerts_fetched.connect(self._nws_fetcher_thread.quit)
            self._nws_fetcher.error.connect(self._nws_fetcher_thread.quit)
            self._nws_fetcher_thread.finished.connect(self._nws_fetcher.deleteLater)
            self._nws_fetcher_thread.finished.connect(lambda: setattr(self, '_nws_fetcher', None))
            self._nws_fetcher_thread.finished.connect(lambda: setattr(self, '_nws_fetcher_thread', None))
            self._nws_fetcher_thread.start()

        if "PAGASA" in self._alert_sources:
            self._pagasa_fetcher = PAGASAAlertFetcher()
            self._pagasa_fetcher.alerts_fetched.connect(self._on_pagasa_alerts)
            self._pagasa_fetcher.error.connect(self._on_error)

            self._pagasa_fetcher_thread = QThread(self)
            self._pagasa_fetcher.moveToThread(self._pagasa_fetcher_thread)
            self._pagasa_fetcher_thread.started.connect(self._pagasa_fetcher.run)
            self._pagasa_fetcher.alerts_fetched.connect(self._pagasa_fetcher_thread.quit)
            self._pagasa_fetcher.error.connect(self._pagasa_fetcher_thread.quit)
            self._pagasa_fetcher_thread.finished.connect(self._pagasa_fetcher.deleteLater)
            self._pagasa_fetcher_thread.finished.connect(lambda: setattr(self, '_pagasa_fetcher', None))
            self._pagasa_fetcher_thread.finished.connect(lambda: setattr(self, '_pagasa_fetcher_thread', None))
            self._pagasa_fetcher_thread.start()

    def _poll_subprocess(self):
        if self._subprocess_fetcher is not None:
            return

        from PySide6.QtCore import QThread

        self._subprocess_fetcher = SubprocessAlertFetcher()
        self._subprocess_fetcher.configure(
            lat=self._lat, lon=self._lon,
            zone=self._zone, marine=self._marine_only,
            sources=self._alert_sources,
        )
        self._subprocess_fetcher.alerts_fetched.connect(self._on_subprocess_alerts)
        self._subprocess_fetcher.error.connect(self._on_error)

        self._subprocess_fetcher_thread = QThread(self)
        self._subprocess_fetcher.moveToThread(self._subprocess_fetcher_thread)
        self._subprocess_fetcher_thread.started.connect(self._subprocess_fetcher.run)
        self._subprocess_fetcher.alerts_fetched.connect(self._subprocess_fetcher_thread.quit)
        self._subprocess_fetcher.error.connect(self._subprocess_fetcher_thread.quit)
        self._subprocess_fetcher_thread.finished.connect(self._subprocess_fetcher.deleteLater)
        self._subprocess_fetcher_thread.finished.connect(lambda: setattr(self, '_subprocess_fetcher', None))
        self._subprocess_fetcher_thread.finished.connect(lambda: setattr(self, '_subprocess_fetcher_thread', None))
        self._subprocess_fetcher_thread.start()

    def _on_subprocess_alerts(self, alerts):
        self._last_nws_alerts = [a for a in alerts if a.get("source") == "NWS"]
        self._last_pagasa_alerts = [a for a in alerts if a.get("source") == "PAGASA"]
        self._process_alerts(alerts, "ALL")

    def _on_nws_alerts(self, alerts):
        self._last_nws_alerts = alerts
        self._process_alerts(alerts, "NWS")

    def _on_pagasa_alerts(self, alerts):
        self._last_pagasa_alerts = alerts
        self._process_alerts(alerts, "PAGASA")

    def _process_alerts(self, alerts, source):
        new_alerts = []
        emergency_alerts = []
        fulemer_alerts = []
        has_non_minor = False
        for a in alerts:
            aid = a.get("id", "")
            if aid and aid not in self._seen_ids:
                self._seen_ids.add(aid)
                new_alerts.append(a)
                if is_minor(a):
                    continue
                has_non_minor = True
                if is_full_emergency(a) or is_pagasa_extreme_flood(a):
                    fulemer_alerts.append(a)
                elif is_emergency(a):
                    emergency_alerts.append(a)

        if new_alerts:
            _save_seen_ids(self._seen_ids)
            for a in new_alerts:
                self.new_alert.emit(a)
            if fulemer_alerts:
                for a in fulemer_alerts:
                    self.fulemer_alert.emit(a)
                play_full_emergency()
            elif emergency_alerts:
                for a in emergency_alerts:
                    self.emergency_alert.emit(a)
                play_emergency(use_soft=self._use_soft_emer)
            elif has_non_minor:
                play_notification(use_modern=self._use_modern_notif)

            # Speak only for emergency alerts (Severe+), excluding full emergency (already handled by siren)
            latest = max(new_alerts, key=lambda a: (
                severity_score(a),
                a.get("effective", "") or a.get("effective_date", "") or ""
            ))
            if is_emergency(latest) and not is_full_emergency(latest) and not is_pagasa_extreme_flood(latest):
                speak_alert(latest, mode="brief")

        self._update_summary()

    def _update_summary(self):
        all_alerts = self._last_nws_alerts + self._last_pagasa_alerts
        by_sev = {}
        by_source = {}
        for a in all_alerts:
            s = a.get("severity", "Unknown")
            by_sev[s] = by_sev.get(s, 0) + 1
            src = a.get("source", "NWS")
            by_source[src] = by_source.get(src, 0) + 1

        parts = [f"{k}: {v}" for k, v in sorted(by_sev.items(),
                 key=lambda x: {"Extreme": 5, "Severe": 4, "Moderate": 3, "Minor": 2, "Unknown": 1}.get(x[0], 0),
                 reverse=True)]
        source_parts = [f"{k}: {v}" for k, v in sorted(by_source.items())]

        if parts:
            summary = f"Weather Alerts ({', '.join(source_parts)}): {', '.join(parts)}"
        else:
            summary = "No active weather alerts"

        self._last_summary = summary
        self.alert_summary.emit(summary)

    def _on_error(self, msg):
        self.poll_error.emit(msg)
        log.warning(f"Alert poll error: {msg}")

    def get_seen_count(self):
        return len(self._seen_ids)

    def clear_seen(self):
        self._seen_ids.clear()
        _save_seen_ids(self._seen_ids)
