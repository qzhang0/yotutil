"""Tests for config module."""

from yotutil.config import Config, DEFAULTS, load_config


def test_default_config():
    config = Config()
    assert config.output_dir == "."
    assert config.audio_quality == "0"
    assert config.embed_metadata is True
    assert config.embed_thumbnail is True


def test_load_config_returns_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", tmp_path / "nonexistent.toml")
    config = load_config()
    assert config.output_dir == DEFAULTS["output_dir"]
    assert config.audio_quality == str(DEFAULTS["audio_quality"])


def test_load_config_reads_toml(tmp_path, monkeypatch):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('output_dir = "/tmp/music"\naudio_quality = 5\n')
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", toml_file)

    config = load_config()
    assert config.output_dir == "/tmp/music"
    assert config.audio_quality == "5"
    assert config.embed_metadata is True  # default preserved
