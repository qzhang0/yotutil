"""Tests for config module."""

from pathlib import Path

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


def test_load_config_reads_player_clients(tmp_path, monkeypatch):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('player_clients = ["ios", "web"]\n')
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", toml_file)

    assert load_config().player_clients == ["ios", "web"]


def test_default_config_leaves_player_clients_unset():
    """None means 'use the downloader's built-in list'."""
    assert Config().player_clients is None


def test_load_config_expands_home_in_output_dir(tmp_path, monkeypatch):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('output_dir = "~/Music"\n')
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", toml_file)

    config = load_config()
    assert config.output_dir == str(Path.home() / "Music")
    assert not config.output_dir.startswith("~")
