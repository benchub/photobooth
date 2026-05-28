"""Typed config loaded from YAML with env-var overrides.

Env vars override the file. Format: PHOTOBOOTH_<SECTION>_<KEY>, e.g.
PHOTOBOOTH_IMMICH_API_KEY=abc123 overrides immich.api_key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ImmichConfig:
    base_url: str = ""
    api_key: str = ""
    album_name: str = "Photobooth"


@dataclass
class CameraConfig:
    capture_target_key: str = "capturetarget"
    capture_target_value: str = "Internal RAM"
    image_format_key: str = "imageformat"
    image_format_value: str = "L"  # R6 short code for "Large fine JPEG"
    auto_poweroff_key: str = "autopoweroff"
    auto_poweroff_value: str = "0"
    init_retries: int = 3


@dataclass
class ChromaConfig:
    hue_low: int = 35
    hue_high: int = 85
    sat_min: int = 60
    val_min: int = 40
    feather_px_preview: int = 5
    feather_px_final: int = 17
    spill_suppress: bool = True
    guided_filter: bool = True


@dataclass
class StripConfig:
    header_text: str = "Photobooth"


@dataclass
class UIConfig:
    inactivity_timeout_s: int = 60
    no_frames_timeout_s: int = 5
    countdown_seconds: int = 3
    capture_count: int = 3
    # Lead time (ms): how early to send the shutter command before the
    # countdown reaches 0, so the R6's actual shutter lag is hidden and
    # the click lines up with the "SNAP!" display. Tune up if the click
    # comes after SNAP, down if it comes before.
    shutter_lead_ms: int = 220


@dataclass
class OutputConfig:
    retain_count: int = 200


@dataclass
class SoundConfig:
    enabled: bool = True
    volume: float = 0.8


@dataclass
class DisplayConfig:
    # Public-facing URL where viewers can see the booth photos (Immich
    # share link, web gallery, etc.). Shown as text + QR on the attract
    # screen. Leave empty to hide.
    share_url: str = ""
    share_caption: str = "See your photos at"


@dataclass
class Config:
    immich: ImmichConfig = field(default_factory=ImmichConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    chroma: ChromaConfig = field(default_factory=ChromaConfig)
    strip: StripConfig = field(default_factory=StripConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    sound: SoundConfig = field(default_factory=SoundConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)

    @property
    def backgrounds_dir(self) -> Path:
        return PROJECT_ROOT / "backgrounds"

    @property
    def output_dir(self) -> Path:
        return PROJECT_ROOT / "output"

    @property
    def raw_dir(self) -> Path:
        return self.output_dir / "raw"

    @property
    def composite_dir(self) -> Path:
        return self.output_dir / "composite"

    @property
    def strips_dir(self) -> Path:
        return self.output_dir / "strips"

    @property
    def pending_upload_dir(self) -> Path:
        return self.output_dir / "pending_upload"

    @property
    def fonts_dir(self) -> Path:
        return PROJECT_ROOT / "assets" / "fonts"

    @property
    def sounds_dir(self) -> Path:
        return PROJECT_ROOT / "assets" / "sounds"

    @property
    def state_dir(self) -> Path:
        return Path.home() / ".photobooth"


def _coerce(value: str, target_type: type) -> Any:
    if target_type is bool:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if target_type is int:
        return int(value)
    if target_type is float:
        return float(value)
    return value


def _apply_env_overrides(cfg: Any, prefix: str = "PHOTOBOOTH") -> None:
    for f in fields(cfg):
        attr = getattr(cfg, f.name)
        if is_dataclass(attr):
            _apply_env_overrides(attr, f"{prefix}_{f.name.upper()}")
            continue
        env_key = f"{prefix}_{f.name.upper()}"
        if env_key in os.environ:
            setattr(cfg, f.name, _coerce(os.environ[env_key], type(attr)))


def _merge_dict(cfg: Any, data: dict[str, Any]) -> None:
    for key, value in data.items():
        if not hasattr(cfg, key):
            continue
        attr = getattr(cfg, key)
        if is_dataclass(attr) and isinstance(value, dict):
            _merge_dict(attr, value)
        else:
            setattr(cfg, key, value)


OVERRIDES_FILENAME = "runtime_overrides.yaml"


def load_config(path: Path | None = None) -> Config:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    cfg = Config()
    yaml_path = path or PROJECT_ROOT / "config.yaml"
    if yaml_path.exists():
        with yaml_path.open() as f:
            data = yaml.safe_load(f) or {}
        _merge_dict(cfg, data)
    # Overlay any runtime overrides written by the on-site settings panel.
    overrides_path = PROJECT_ROOT / OVERRIDES_FILENAME
    if overrides_path.exists():
        try:
            with overrides_path.open() as f:
                data = yaml.safe_load(f) or {}
            _merge_dict(cfg, data)
        except Exception:
            pass  # malformed overrides shouldn't break startup
    _apply_env_overrides(cfg)
    _validate(cfg)
    _ensure_dirs(cfg)
    return cfg


def write_runtime_overrides(updates: dict) -> Path:
    """Persist a partial config dict to runtime_overrides.yaml. Merges with
    any existing overrides; only the keys provided here are touched."""
    overrides_path = PROJECT_ROOT / OVERRIDES_FILENAME
    existing: dict = {}
    if overrides_path.exists():
        try:
            with overrides_path.open() as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            existing = {}
    _deep_merge(existing, updates)
    with overrides_path.open("w") as f:
        yaml.safe_dump(existing, f, sort_keys=False, default_flow_style=False)
    return overrides_path


def clear_runtime_overrides() -> None:
    overrides_path = PROJECT_ROOT / OVERRIDES_FILENAME
    if overrides_path.exists():
        overrides_path.unlink()


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def _validate(cfg: Config) -> None:
    if not cfg.immich.base_url:
        raise ConfigError("immich.base_url is required (set in config.yaml or PHOTOBOOTH_IMMICH_BASE_URL)")
    if not cfg.immich.api_key:
        raise ConfigError("immich.api_key is required (set in config.yaml or PHOTOBOOTH_IMMICH_API_KEY)")
    if cfg.ui.capture_count < 1:
        raise ConfigError("ui.capture_count must be >= 1")
    if not (0 <= cfg.chroma.hue_low < cfg.chroma.hue_high <= 179):
        raise ConfigError("chroma hue range must satisfy 0 <= hue_low < hue_high <= 179")


def _ensure_dirs(cfg: Config) -> None:
    # state_dir is created lazily by callers that need it (Immich client) —
    # writing under ~ may be blocked in sandboxed test environments.
    for d in (cfg.output_dir, cfg.raw_dir, cfg.composite_dir, cfg.strips_dir,
              cfg.pending_upload_dir, cfg.backgrounds_dir):
        d.mkdir(parents=True, exist_ok=True)


class ConfigError(RuntimeError):
    pass
