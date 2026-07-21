from core.cli_api import embedding_service_diagnosis


def test_embedding_service_diagnosis_prefers_published_default():
    name, steps = embedding_service_diagnosis("http://host.docker.internal:42666/api/embed")

    assert name == "embeddinggemma local sidecar"
    assert any("embeddinggemma" in step for step in steps)
    assert not any("embeddinggemma-metal" in step for step in steps)


def test_embedding_service_diagnosis_flags_legacy_port():
    name, steps = embedding_service_diagnosis("http://host.docker.internal:11434/api/embed")

    assert name == "legacy embeddinggemma sidecar configuration"
    assert any("42666" in step for step in steps)


def test_local_embedding_binary_uses_published_command(monkeypatch, tmp_path):
    from apps import hexis_cli

    binary = tmp_path / "embeddinggemma"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)

    monkeypatch.setattr(hexis_cli.shutil, "which", lambda name: str(binary) if name == "embeddinggemma" else None)

    assert hexis_cli._local_embedding_binary() == binary


def test_local_embedding_binary_falls_back_to_installer_default(monkeypatch, tmp_path):
    from apps import hexis_cli

    home = tmp_path
    binary = home / ".local" / "bin" / "embeddinggemma"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(hexis_cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(hexis_cli.Path, "home", staticmethod(lambda: home))

    assert hexis_cli._local_embedding_binary() == binary


def test_local_embedding_sidecar_detects_published_port(monkeypatch, tmp_path):
    from apps import hexis_cli

    env_file = tmp_path / ".env"
    env_file.write_text("EMBEDDING_SERVICE_URL=http://host.docker.internal:42666/api/embed\n", encoding="utf-8")
    monkeypatch.delenv("EMBEDDING_SERVICE_URL", raising=False)

    assert hexis_cli._uses_local_embedding_sidecar(env_file) is True


def test_local_embedding_sidecar_rejects_legacy_port(monkeypatch, tmp_path):
    from apps import hexis_cli

    env_file = tmp_path / ".env"
    env_file.write_text("EMBEDDING_SERVICE_URL=http://host.docker.internal:11434/api/embed\n", encoding="utf-8")
    monkeypatch.delenv("EMBEDDING_SERVICE_URL", raising=False)

    assert hexis_cli._uses_local_embedding_sidecar(env_file) is False
    assert hexis_cli._uses_legacy_embedding_sidecar_port(env_file) is True
