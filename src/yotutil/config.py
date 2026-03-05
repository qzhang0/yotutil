"""Configuration loading with defaults and TOML override."""

from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_DIR = Path.home() / ".config" / "yotutil"
CONFIG_FILE = CONFIG_DIR / "config.toml"

DEFAULTS = {
    "output_dir": ".",
    "audio_quality": "0",  # 0 = best
    "embed_metadata": True,
    "embed_thumbnail": True,
}


@dataclass
class Config:
    output_dir: str = DEFAULTS["output_dir"]
    audio_quality: str = DEFAULTS["audio_quality"]
    embed_metadata: bool = DEFAULTS["embed_metadata"]
    embed_thumbnail: bool = DEFAULTS["embed_thumbnail"]


def load_config() -> Config:
    """Load config from TOML file, falling back to defaults."""
    if not CONFIG_FILE.exists():
        return Config()

    with open(CONFIG_FILE, "rb") as f:
        data = tomllib.load(f)

    return Config(
        output_dir=data.get("output_dir", DEFAULTS["output_dir"]),
        audio_quality=str(data.get("audio_quality", DEFAULTS["audio_quality"])),
        embed_metadata=data.get("embed_metadata", DEFAULTS["embed_metadata"]),
        embed_thumbnail=data.get("embed_thumbnail", DEFAULTS["embed_thumbnail"]),
    )
