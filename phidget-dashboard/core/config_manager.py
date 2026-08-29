"""
@file: core/config_manager.py
@version: 1.0.0
@date: 2026-08-29
@description: Zentraler Manager fuer Konfigurations- und Geraetedateien (YAML).
              Implementiert Schema-Validierung, konsistente Pfadauflösung,
              atomare Schreibzugriffe und File-Locking zur Vermeidung von Race Conditions.
@author: Patrick Staehli
"""

import os
import glob
import yaml
import fcntl
import tempfile
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict


# ==========================================
# SCHEMA DEFINITIONEN
# ==========================================

@dataclass
class ProbeDetectionConfig:
    delta_t_min: Optional[float] = None
    slope_min: Optional[float] = None
    rot_peak_min: Optional[float] = None

@dataclass
class TurnaroundDetectionConfig:
    sg_window: int = 31
    cooling_slope_min: float = -0.0003
    reheating_slope_min: float = 0.0003
    min_cooling_delta: float = 0.20

@dataclass
class SettingDetectionConfig:
    sg_window: int = 21
    poly_order: int = 2
    lookback_sec: int = 120
    min_samples: int = 15
    accel_min: float = 0.000010
    slope_min: float = 0.0002
    reheating_delta_min: float = 0.15
    fallback_samples: int = 5
    fallback_step_min: float = 0.020
    fallback_reheating_min: float = 0.20

@dataclass
class ChannelMetadataConfig:
    location: str = "Extern"
    interface: str = "Lieferschein"
    custom_string: str = ""
    recipe_id: str = ""
    cement_name: str = "cem100"
    cement_id: str = ""

@dataclass
class DeviceConfig:
    name: str
    device_id: str
    box_label: str = ""
    ntfy_channel: str = "Concretum"
    timezone: str = "Europe/Zurich"
    channel_count: int = 4
    serial: Optional[int] = None
    ip_lan: Optional[str] = None
    mac_lan: Optional[str] = None
    mac_wlan: Optional[str] = None
    workstation: Optional[str] = None
    channel_recording_enabled: bool = True
    auto_detection_enabled: bool = False
    turnaround_detection_enabled: bool = False
    ntfy_enabled: bool = False
    auto_reset_after_30m: bool = True
    channel_labels: Dict[str, str] = field(default_factory=dict)
    channel_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    probe_detection: Optional[Dict[str, Any]] = None
    turnaround_detection: Optional[Dict[str, Any]] = None
    setting_detection: Optional[Dict[str, Any]] = None


# ==========================================
# CENTRAL CONFIG MANAGER
# ==========================================

class ConfigManager:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_path = os.environ.get("YAML_CONFIG_PATH", "")

        if env_path:
            if env_path.endswith((".yaml", ".yml")):
                env_path = os.path.dirname(env_path)
            if os.path.basename(env_path) == "devices":
                self.devices_dir = env_path
                self.config_dir = os.path.dirname(env_path)
            else:
                self.config_dir = env_path
                self.devices_dir = os.path.join(env_path, "devices")
        else:
            self.config_dir = os.path.join(self.base_dir, "config")
            self.devices_dir = os.path.join(self.config_dir, "devices")

        os.makedirs(self.devices_dir, exist_ok=True)

    def get_server_config(self) -> Dict[str, Any]:
        """Laedt die Server.yaml Konfiguration."""
        server_file = os.path.join(self.devices_dir, "Server.yaml")
        if not os.path.exists(server_file):
            server_file = os.path.join(self.config_dir, "Server.yaml")

        if os.path.exists(server_file):
            try:
                with open(server_file, "r", encoding="utf-8") as f:
                    content = yaml.safe_load(f) or {}
                    return content.get("Server", content)
            except Exception as e:
                print(f"[ConfigManager Error Server.yaml] {e}", flush=True)
        return {}

    def get_device_file_path(self, yaml_key: str) -> str:
        clean_key = os.path.basename(yaml_key).replace(".yaml", "").strip()
        return os.path.join(self.devices_dir, f"{clean_key}.yaml")

    def load_all_raw(self) -> Dict[str, Any]:
        """Laedt Server.yaml und alle Geraete-Dateien als Dictionary zusammen."""
        combined = {"Server": self.get_server_config()}
        pattern = os.path.join(self.devices_dir, "*.yaml")

        for file_path in glob.glob(pattern):
            filename = os.path.basename(file_path)
            if filename.lower() == "server.yaml":
                continue
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    try:
                        dev_data = yaml.safe_load(f) or {}
                        if isinstance(dev_data, dict):
                            combined.update(dev_data)
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception as e:
                print(f"[ConfigManager Load Error {filename}] {e}", flush=True)

        return combined

    def get_device_map(self) -> Dict[str, Dict[str, Any]]:
        """Gibt eine Lookup-Map fuer schnellen Zugriff nach Dateiname, Key oder device_id zurueck."""
        device_map = {}
        raw_data = self.load_all_raw()

        pattern = os.path.join(self.devices_dir, "*.yaml")
        for file_path in glob.glob(pattern):
            filename = os.path.basename(file_path)
            if filename.lower() == "server.yaml":
                continue
            file_stem = os.path.splitext(filename)[0]

            for root_key, val in raw_data.items():
                if root_key == "Server" or not isinstance(val, dict):
                    continue

                dev_id = str(val.get("device_id", "")).strip().lower()
                name = str(val.get("name", "")).strip()

                meta = {
                    "file_path": file_path,
                    "file_name": filename,
                    "root_key": root_key,
                    "yaml_key": root_key,
                    "device_id": dev_id,
                    "name": name,
                    "data": val
                }

                device_map[file_stem.lower()] = meta
                device_map[root_key.lower()] = meta
                if dev_id:
                    device_map[dev_id] = meta
                if name:
                    device_map[name.lower()] = meta

        return device_map

    def save_device_config(self, yaml_key: str, data: Dict[str, Any]) -> bool:
        """Speichert eine Geraetekonfiguration atomar und thread-/prozess-sicher ab."""
        if not yaml_key or yaml_key.lower() == "server":
            return False

        target_file = self.get_device_file_path(yaml_key)
        payload = {yaml_key: data}

        try:
            # Atomar via Tempfile im selben Verzeichnis (fuer os.replace auf demselben Filesystem)
            temp_fd, temp_path = tempfile.mkstemp(dir=self.devices_dir, prefix=f".{yaml_key}_", suffix=".tmp")
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    yaml.dump(payload, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            os.replace(temp_path, target_file)
            return True
        except Exception as e:
            print(f"[ConfigManager Save Error {yaml_key}] {e}", flush=True)
            if 'temp_path' in locals() and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return False

    def delete_device_config(self, yaml_key: str) -> bool:
        """Loescht eine Geraetedatei sicher."""
        if not yaml_key or yaml_key.lower() == "server":
            return False

        file_path = self.get_device_file_path(yaml_key)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                return True
            except Exception as e:
                print(f"[ConfigManager Delete Error {yaml_key}] {e}", flush=True)
                return False
        return True

    def get_parsed_config(self) -> Dict[str, Any]:
        """Bereitet alle Box-Konfigurationen konsistent fuer Webviews & Dashboards auf."""
        raw = self.load_all_raw()
        server_cfg = raw.get("Server", {})

        boxes = []
        for key, val in raw.items():
            if key == "Server" or not isinstance(val, dict):
                continue

            dev_id = val.get("device_id", key)
            ch_count = int(val.get("channel_count", 4))
            custom_labels = val.get("channel_labels", {})

            channels = []
            for i in range(ch_count):
                ch_id = f"temp{i}"
                ch_label = custom_labels.get(ch_id, f"Kanal {i + 1}")
                channels.append({"id": ch_id, "label": ch_label})

            boxes.append({
                "yaml_key": key,
                "id": dev_id,
                "name": val.get("name", key),
                "box_label": val.get("box_label", ""),
                "topic": val.get("ntfy_channel", "Concretum"),
                "timezone": val.get("timezone", server_cfg.get("timezone", "Europe/Zurich")),
                "channel_count": ch_count,
                "serial": val.get("serial"),
                "ip_lan": val.get("ip_lan"),
                "mac_lan": val.get("mac_lan"),
                "mac_wlan": val.get("mac_wlan"),
                "workstation": val.get("workstation"),
                "probe_detection": val.get("probe_detection", {}),
                "auto_detection_enabled": val.get("auto_detection_enabled", False),
                "turnaround_detection": val.get("turnaround_detection", {}),
                "turnaround_detection_enabled": val.get("turnaround_detection_enabled", False),
                "setting_detection": val.get("setting_detection", {}),
                "channel_recording_enabled": val.get("channel_recording_enabled", True),
                "ntfy_enabled": val.get("ntfy_enabled", False),
                "auto_reset_after_30m": val.get("auto_reset_after_30m", True),
                "channels": channels,
                "channel_labels": custom_labels,
                "channel_metadata": val.get("channel_metadata", {})
            })

        return {"server": server_cfg, "boxes": boxes}

    def update_box_switches(
        self, 
        box_id: str, 
        logging_state: bool, 
        auto_detect: Optional[bool] = None, 
        turnaround: Optional[bool] = None, 
        ntfy_state: Optional[bool] = None
    ) -> bool:
        """Aktualisiert selektiv Status-Flags einer Box."""
        dev_map = self.get_device_map()
        meta = dev_map.get(str(box_id).strip().lower())
        if not meta:
            return False

        target_key = meta["yaml_key"]
        raw = self.load_all_raw()
        box_data = raw.get(target_key, {})
        if not box_data:
            return False

        box_data["channel_recording_enabled"] = logging_state
        if auto_detect is not None:
            box_data["auto_detection_enabled"] = auto_detect
        if turnaround is not None:
            box_data["turnaround_detection_enabled"] = turnaround
        if ntfy_state is not None:
            box_data["ntfy_enabled"] = ntfy_state

        return self.save_device_config(target_key, box_data)


# Globales Singleton fuer die Anwendung
config_manager = ConfigManager()