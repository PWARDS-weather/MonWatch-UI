from datetime import datetime, timezone
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel, QSlider, QComboBox, QPushButton, QListWidget, QListWidgetItem
from ..services.utils import compute_sun_azel


_CB_STYLE = (
    "QCheckBox { color: #EEE; padding: 4px 5px; font-size: 10px; "
    "background: #2D2D2D; border-radius: 3px; }"
    "QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; "
    "background-color: #2D2D2D; border: 2px solid #555; }"
    "QCheckBox::indicator:checked { background-color: #4CAF50; border: 2px solid #4CAF50; }"
    "QCheckBox:hover:!disabled { background: #3D3D3D; }"
)

_SLIDER_STYLE = (
    "QSlider::groove:horizontal { height: 4px; background: #444; border-radius: 2px; }"
    "QSlider::handle:horizontal { background: #AAA; width: 10px; margin: -3px 0; border-radius: 4px; }"
)

_BTN_STYLE = (
    "QPushButton{background:#2D2D2D;color:#CCC;border:1px solid #555;"
    "border-radius:3px;padding:2px 8px;font-size:10px;}"
    "QPushButton:hover{background:#3D3D3D;}"
)

_LIST_STYLE = (
    "QListWidget{background:#1f1f1f;color:#ddd;border:1px solid #444;"
    "font-size:10px;outline:0;}"
    "QListWidget::item:selected{background:#4c1d95;color:#fff;}"
)


class BroadcastOverlayPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(3)
        layout.setContentsMargins(4, 4, 4, 4)

        # Forecast overlay
        header = QLabel("FORECAST OVERLAY")
        header.setStyleSheet("color: #5D8AA8; font-weight: bold; font-size: 10px; padding-bottom: 2px;")
        layout.addWidget(header)

        ov_row = QHBoxLayout()
        ov_row.setSpacing(4)
        self.overlay_combo = QComboBox()
        self.overlay_combo.addItems(["None", "Temperature", "Precipitation", "Wind"])
        self.overlay_combo.setStyleSheet(
            "QComboBox{background:#2D2D2D;color:#CCC;border:1px solid #444;padding:2px 4px;font-size:10px;}"
        )
        ov_row.addWidget(QLabel("Overlay:"))
        ov_row.addWidget(self.overlay_combo, 1)
        layout.addLayout(ov_row)

        # Opacity slider
        op_row = QHBoxLayout()
        op_row.setSpacing(4)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(40)
        self.opacity_slider.setStyleSheet(_SLIDER_STYLE)
        self.opacity_label = QLabel("40%")
        self.opacity_label.setStyleSheet("color:#AAA;font-size:9px;")
        op_row.addWidget(QLabel("Opacity:"))
        op_row.addWidget(self.opacity_slider, 1)
        op_row.addWidget(self.opacity_label)
        layout.addLayout(op_row)

        layout.addSpacing(6)

        # ── Forecast data source ──────────────────────────────────────────
        src_header = QLabel("FORECAST SOURCE")
        src_header.setStyleSheet(
            "color: #f472b6; font-weight: bold; font-size: 10px; padding-bottom: 2px;"
        )
        layout.addWidget(src_header)

        # Method selector: Raw (GRIB2 / Herbie) vs Processed (API)
        method_row = QHBoxLayout()
        method_row.setSpacing(4)
        self.method_combo = QComboBox()
        self.method_combo.addItems(["Raw Forecast", "Processed Forecast"])
        self.method_combo.setStyleSheet(
            "QComboBox{background:#2D2D2D;color:#CCC;border:1px solid #444;padding:2px 4px;font-size:10px;}"
            "QComboBox::drop-down{width:16px;}"
        )
        method_row.addWidget(QLabel("Method:"))
        method_row.addWidget(self.method_combo, 1)
        layout.addLayout(method_row)

        # ── Raw forecast controls (Herbie GFS / ECMWF / Open-Meteo) ──────
        self._raw_widget = QWidget()
        raw_layout = QVBoxLayout(self._raw_widget)
        raw_layout.setSpacing(3)
        raw_layout.setContentsMargins(4, 2, 4, 2)

        src_row = QHBoxLayout()
        src_row.setSpacing(4)
        self.source_combo = QComboBox()
        self.source_combo.addItems(["Open-Meteo", "GFS (Herbie)", "ECMWF (Herbie)"])
        self.source_combo.setStyleSheet(
            "QComboBox{background:#2D2D2D;color:#CCC;border:1px solid #444;padding:2px 4px;font-size:10px;}"
            "QComboBox::drop-down{width:16px;}"
        )
        src_row.addWidget(QLabel("Source:"))
        src_row.addWidget(self.source_combo, 1)
        raw_layout.addLayout(src_row)

        model_row = QHBoxLayout()
        model_row.setSpacing(4)
        self.model_combo = QComboBox()
        self.model_combo.setStyleSheet(
            "QComboBox{background:#2D2D2D;color:#CCC;border:1px solid #444;padding:2px 4px;font-size:10px;}"
            "QComboBox::drop-down{width:16px;}"
        )
        model_row.addWidget(QLabel("Model:"))
        model_row.addWidget(self.model_combo, 1)
        raw_layout.addLayout(model_row)

        fh_row = QHBoxLayout()
        fh_row.setSpacing(4)
        self.fxx_slider = QSlider(Qt.Horizontal)
        self.fxx_slider.setRange(0, 384)
        self.fxx_slider.setValue(0)
        self.fxx_slider.setStyleSheet(_SLIDER_STYLE)
        self.fxx_label = QLabel("F+00")
        self.fxx_label.setStyleSheet("color:#AAA;font-size:9px;")
        fh_row.addWidget(QLabel("Fxx:"))
        fh_row.addWidget(self.fxx_slider, 1)
        fh_row.addWidget(self.fxx_label)
        raw_layout.addLayout(fh_row)

        layout.addWidget(self._raw_widget)

        # ── Processed forecast controls (Windy / AccuWeather / OpenWeatherMap) ──
        self._processed_widget = QWidget()
        proc_layout = QVBoxLayout(self._processed_widget)
        proc_layout.setSpacing(3)
        proc_layout.setContentsMargins(4, 2, 4, 2)

        prov_row = QHBoxLayout()
        prov_row.setSpacing(4)
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["Windy", "AccuWeather", "OpenWeatherMap"])
        self.provider_combo.setStyleSheet(
            "QComboBox{background:#2D2D2D;color:#CCC;border:1px solid #444;padding:2px 4px;font-size:10px;}"
            "QComboBox::drop-down{width:16px;}"
        )
        prov_row.addWidget(QLabel("Provider:"))
        prov_row.addWidget(self.provider_combo, 1)
        proc_layout.addLayout(prov_row)

        api_row = QHBoxLayout()
        api_row.setSpacing(4)
        self.api_key_edit = type(self).api_key_edit if hasattr(type(self), 'api_key_edit') else None
        if self.api_key_edit is None:
            from PySide6.QtWidgets import QLineEdit
            self.api_key_edit = QLineEdit()
            self.api_key_edit.setPlaceholderText("API key (from Settings)")
            self.api_key_edit.setEchoMode(QLineEdit.Password)
            self.api_key_edit.setStyleSheet(
                "QLineEdit{background:#2D2D2D;color:#CCC;border:1px solid #444;padding:2px 4px;font-size:10px;}"
            )
            type(self).api_key_edit = self.api_key_edit
        api_row.addWidget(QLabel("API Key:"))
        api_row.addWidget(self.api_key_edit, 1)
        proc_layout.addLayout(api_row)

        layout.addWidget(self._processed_widget)

        self._refresh_btn = QPushButton("Refresh Now")
        self._refresh_btn.setStyleSheet(_BTN_STYLE)
        self._refresh_btn.clicked.connect(self._on_refresh_forecast)
        layout.addWidget(self._refresh_btn)

        self.method_combo.currentIndexChanged.connect(self._on_method_changed)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self._on_method_changed(0)
        self._on_source_changed(0)

        layout.addSpacing(6)

        # Globe layers visibility
        layer_header = QLabel("LAYERS")
        layer_header.setStyleSheet("color: #f472b6; font-weight: bold; font-size: 10px; padding-bottom: 2px;")
        layout.addWidget(layer_header)

        self.layer_checkboxes = {}
        layers = [
            ("Satellite", "satellite", True),
            ("Clouds", "clouds", True),
            ("Forecast", "forecast", True),
            ("Atmosphere", "atmosphere", True),
            ("Grid", "grid", True),
            ("Coastlines", "coastlines", True),
            ("Topographic Land", "topo_land", False),
            ("Topographic Water", "topo_water", False),
        ]
        for label, key, default in layers:
            cb = QCheckBox(label)
            cb.setChecked(default)
            cb.setStyleSheet(_CB_STYLE)
            self.layer_checkboxes[key] = cb
            layout.addWidget(cb)
            cb.toggled.connect(lambda checked, k=key: self._on_layer_toggle(k, checked))

        layout.addSpacing(6)

        # Layer ordering
        order_header = QLabel("LAYER ORDER")
        order_header.setStyleSheet("color: #5D8AA8; font-weight: bold; font-size: 10px; padding-bottom: 2px;")
        layout.addWidget(order_header)

        self.order_list = QListWidget()
        self.order_list.setStyleSheet(_LIST_STYLE)
        self.order_list.setMaximumHeight(120)
        layout.addWidget(self.order_list)

        order_btn_row = QHBoxLayout()
        self.up_btn = QPushButton("\u25B2 Up")
        self.up_btn.setStyleSheet(_BTN_STYLE)
        self.down_btn = QPushButton("\u25BC Down")
        self.down_btn.setStyleSheet(_BTN_STYLE)
        order_btn_row.addWidget(self.up_btn)
        order_btn_row.addWidget(self.down_btn)
        layout.addLayout(order_btn_row)

        self.up_btn.clicked.connect(self._on_move_up)
        self.down_btn.clicked.connect(self._on_move_down)

        layout.addSpacing(6)

        # ── Sun direction ─────────────────────────────────────────────────
        sun_header = QLabel("SUN DIRECTION")
        sun_header.setStyleSheet(
            "color: #FFA726; font-weight: bold; font-size: 10px; padding-bottom: 2px;"
        )
        layout.addWidget(sun_header)

        az_row = QHBoxLayout()
        az_row.setSpacing(4)
        self.sun_az_slider = QSlider(Qt.Horizontal)
        self.sun_az_slider.setRange(0, 360)
        self.sun_az_slider.setValue(180)
        self.sun_az_slider.setStyleSheet(_SLIDER_STYLE)
        self.sun_az_label = QLabel("180°")
        self.sun_az_label.setStyleSheet("color:#AAA;font-size:9px;")
        az_row.addWidget(QLabel("Azimuth:"))
        az_row.addWidget(self.sun_az_slider, 1)
        az_row.addWidget(self.sun_az_label)
        layout.addLayout(az_row)

        el_row = QHBoxLayout()
        el_row.setSpacing(4)
        self.sun_el_slider = QSlider(Qt.Horizontal)
        self.sun_el_slider.setRange(0, 90)
        self.sun_el_slider.setValue(45)
        self.sun_el_slider.setStyleSheet(_SLIDER_STYLE)
        self.sun_el_label = QLabel("45°")
        self.sun_el_label.setStyleSheet("color:#AAA;font-size:9px;")
        el_row.addWidget(QLabel("Elevation:"))
        el_row.addWidget(self.sun_el_slider, 1)
        el_row.addWidget(self.sun_el_label)
        layout.addLayout(el_row)

        self.sun_az_slider.valueChanged.connect(self._on_sun_az_changed)
        self.sun_el_slider.valueChanged.connect(self._on_sun_changed)

        layout.addSpacing(6)

        # Overlays (2D annotations - placeholder)
        header2 = QLabel("OVERLAYS")
        header2.setStyleSheet("color: #5D8AA8; font-weight: bold; font-size: 10px; padding-bottom: 2px;")
        layout.addWidget(header2)

        self.checkboxes = {}
        overlays = [
            ("PAR (PAGASA AoR)", "par"),
            ("Target Area", "target"),
            ("TCAD", "tcad"),
            ("Grid", "grid"),
            ("Coastlines", "coast"),
            ("TC Track Labels", "track_labels"),
            ("Info Box", "info_box"),
        ]
        for label, key in overlays:
            cb = QCheckBox(label)
            cb.setStyleSheet(_CB_STYLE)
            self.checkboxes[key] = cb
            layout.addWidget(cb)
            cb.toggled.connect(lambda checked, k=key: self._on_toggle(k, checked))

        layout.addStretch()

        self.overlay_combo.currentTextChanged.connect(self._on_overlay_changed)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.method_combo.currentIndexChanged.connect(self._on_method_changed)
        self.source_combo.currentIndexChanged.connect(self._on_source_model_changed)
        self.model_combo.currentIndexChanged.connect(self._on_source_model_changed)
        self.fxx_slider.valueChanged.connect(self._on_fxx_changed)
        self.provider_combo.currentIndexChanged.connect(self._on_refresh_forecast)

    def _on_method_changed(self, idx: int):
        is_raw = self.method_combo.currentText() == "Raw Forecast"
        self._raw_widget.setVisible(is_raw)
        self._processed_widget.setVisible(not is_raw)
        self._write_forecast_prefs()
        ov = self.overlay_combo.currentText()
        if ov != "None":
            self._on_refresh_forecast()

    def _on_source_changed(self, idx: int):
        source = self.source_combo.currentText()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        max_fxx = 72
        if source == "Open-Meteo":
            self.model_combo.addItem("Default")
            self.model_combo.setEnabled(False)
            max_fxx = 72
        elif source == "GFS (Herbie)":
            self.model_combo.addItems(["0.25°", "0.50°"])
            self.model_combo.setEnabled(True)
            max_fxx = 384
        else:
            self.model_combo.addItems(["HRES", "ENS"])
            self.model_combo.setEnabled(True)
            max_fxx = 240
        self.model_combo.blockSignals(False)
        self.fxx_slider.blockSignals(True)
        self.fxx_slider.setRange(0, max_fxx)
        self.fxx_slider.blockSignals(False)
        self._on_source_model_changed()

    def _on_source_model_changed(self):
        try:
            from src.services.forecast_provider import clear_herbie_cache
            clear_herbie_cache()
        except Exception:
            pass
        self._write_forecast_prefs()
        self._on_refresh_forecast()

    def _on_fxx_changed(self, val: int):
        self.fxx_label.setText(f"F+{val:03d}")
        self._write_forecast_prefs()
        ov = self.overlay_combo.currentText()
        if ov != "None":
            self._on_refresh_forecast()

    def _model_value(self) -> str:
        text = self.model_combo.currentText()
        src = self.source_combo.currentText()
        if "GFS" in src:
            return "0p25" if "25" in text else "0p50"
        elif "ECMWF" in src:
            return "oper" if text == "HRES" else "ens"
        return ""

    def _fxx_value(self) -> int:
        return self.fxx_slider.value()

    def _method(self) -> str:
        return "raw" if self.method_combo.currentText() == "Raw Forecast" else "processed"

    def _provider(self) -> str:
        return self.provider_combo.currentText().lower()

    def _on_refresh_forecast(self):
        try:
            from src.services.forecast_provider import (
                export_forecast_texture,
                export_processed_forecast_texture,
            )
            ov = self.overlay_combo.currentText()
            var = "temp"
            if ov == "Wind":
                var = "wind"
            elif ov == "Precipitation":
                var = "precip"

            if self._method() == "raw":
                fxx = self._fxx_value()
                src = self.source_combo.currentText()
                if "GFS" in src:
                    export_forecast_texture(source="gfs", model=self._model_value(), fxx=fxx, variable=var)
                elif "ECMWF" in src:
                    export_forecast_texture(source="ecmwf", model=self._model_value(), fxx=fxx, variable=var)
                else:
                    export_forecast_texture(source="openmeteo", variable=var)
                if var == "wind":
                    self._trigger_wind_export()
            else:
                api_key = self.api_key_edit.text().strip() or None
                export_processed_forecast_texture(
                    provider=self._provider(), variable=var, api_key=api_key,
                )
            self._write_forecast_prefs()
        except Exception:
            pass

    def _write_forecast_prefs(self):
        try:
            import json, os
            from PySide6.QtCore import QStandardPaths
            d = QStandardPaths.writableLocation(QStandardPaths.TempLocation)
            method = self._method()
            provider = self._provider() if method == "processed" else ""
            src = self.source_combo.currentText()
            if "GFS" in src:
                s, m = "gfs", self._model_value()
            elif "ECMWF" in src:
                s, m = "ecmwf", self._model_value()
            else:
                s, m = "openmeteo", ""
            ov = self.overlay_combo.currentText()
            var = "temp"
            if ov == "Wind":
                var = "wind"
            elif ov == "Precipitation":
                var = "precip"
            with open(os.path.join(d, "broadcast_forecast_source.json"), "w") as f:
                json.dump({
                    "method": method,
                    "provider": provider,
                    "source": s,
                    "model": m,
                    "variable": var,
                    "fxx": self._fxx_value(),
                }, f)
        except Exception:
            pass

    def set_layer_order(self, order: list):
        self.order_list.blockSignals(True)
        self.order_list.clear()
        for name in order:
            item = QListWidgetItem(name.capitalize())
            item.setData(Qt.UserRole, name)
            self.order_list.addItem(item)
        self.order_list.blockSignals(False)

    def _on_layer_toggle(self, key: str, checked: bool):
        w = self.window()
        if w and hasattr(w, 'globe'):
            w.globe.set_layer_visible(key, checked)

    def _on_move_up(self):
        row = self.order_list.currentRow()
        if row <= 0:
            return
        name = self.order_list.item(row).data(Qt.UserRole)
        w = self.window()
        if w and hasattr(w, 'globe'):
            w.globe.move_layer_up(name)

    def _on_move_down(self):
        row = self.order_list.currentRow()
        if row < 0 or row >= self.order_list.count() - 1:
            return
        name = self.order_list.item(row).data(Qt.UserRole)
        w = self.window()
        if w and hasattr(w, 'globe'):
            w.globe.move_layer_down(name)

    def _on_toggle(self, key: str, checked: bool):
        pass

    def _on_overlay_changed(self, text: str):
        enabled = text != "None"
        self.opacity_slider.setEnabled(enabled)
        if not enabled:
            self._set_forecast_opacity(0.0)
            self._set_wind_particles(False)
        else:
            self._on_opacity_changed(self.opacity_slider.value())
            self._set_wind_particles(text == "Wind")
            self._on_refresh_forecast()
        self._write_forecast_prefs()

    def _on_opacity_changed(self, val: int):
        opacity = val / 100.0
        self.opacity_label.setText(f"{val}%")
        self._set_forecast_opacity(opacity)

    def _set_forecast_opacity(self, opacity: float):
        w = self.window()
        if w and hasattr(w, 'globe'):
            w.globe.set_forecast_opacity(opacity)

    def _set_wind_particles(self, visible: bool):
        w = self.window()
        if w and hasattr(w, 'globe'):
            w.globe.set_wind_particles_visible(visible)

    def _on_sun_az_changed(self, val: int):
        if val >= 360:
            self.sun_az_slider.blockSignals(True)
            self.sun_az_slider.setValue(val - 360)
            self.sun_az_slider.blockSignals(False)
            val = val - 360
        elif val < 0:
            self.sun_az_slider.blockSignals(True)
            self.sun_az_slider.setValue(val + 360)
            self.sun_az_slider.blockSignals(False)
            val = val + 360
        self.sun_az_label.setText(f"{val}°")
        w = self.window()
        if w and hasattr(w, 'globe'):
            w.globe.set_sun_azel(val, self.sun_el_slider.value())

    def _on_sun_changed(self):
        el = self.sun_el_slider.value()
        self.sun_el_label.setText(f"{el}°")
        w = self.window()
        if w and hasattr(w, 'globe'):
            w.globe.set_sun_azel(self.sun_az_slider.value(), el)

    def set_sun_from_datetime(self, dt: datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        w = self.window()
        cam_lat = 14.6
        try:
            if w and hasattr(w, 'globe'):
                cam_lat = w.globe.camera().lat
        except Exception:
            pass
        az_deg, el_deg = compute_sun_azel(dt, lat=cam_lat)
        self.sun_az_slider.blockSignals(True)
        self.sun_el_slider.blockSignals(True)
        self.sun_az_slider.setValue(int(round(az_deg)))
        self.sun_el_slider.setValue(int(round(el_deg)))
        self.sun_az_label.setText(f"{int(round(az_deg))}°")
        self.sun_el_label.setText(f"{int(round(el_deg))}°")
        self.sun_az_slider.blockSignals(False)
        self.sun_el_slider.blockSignals(False)
        w = self.window()
        if w and hasattr(w, 'globe'):
            w.globe.set_sun_azel(az_deg, el_deg)

    def _trigger_wind_export(self):
        try:
            from src.services.forecast_provider import export_wind_field_textures
            if self._method() == "raw":
                fxx = self._fxx_value()
                src = self.source_combo.currentText()
                if "GFS" in src:
                    export_wind_field_textures(source="gfs", model=self._model_value(), fxx=fxx)
                elif "ECMWF" in src:
                    export_wind_field_textures(source="ecmwf", model=self._model_value(), fxx=fxx)
        except Exception:
            pass
