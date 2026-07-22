from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _service_block(compose_path: str, service: str) -> str:
    text = (ROOT / compose_path).read_text(encoding="utf-8")
    marker = f"  {service}:\n"
    start = text.index(marker)
    next_service = text.find("\n  ", start + len(marker))
    while next_service != -1 and text[next_service + 3] in {" ", "#"}:
        next_service = text.find("\n  ", next_service + 1)
    return text[start:] if next_service == -1 else text[start:next_service]


def test_source_compose_starts_core_workers_by_default():
    for name in ("rabbitmq", "heartbeat_worker", "maintenance_worker"):
        assert "\n    profiles:" not in _service_block("docker-compose.yml", name)

    assert 'command: ["hexis-worker", "--mode", "heartbeat"]' in _service_block(
        "docker-compose.yml", "heartbeat_worker"
    )
    assert 'command: ["hexis-worker", "--mode", "maintenance"]' in _service_block(
        "docker-compose.yml", "maintenance_worker"
    )
    assert "\n    profiles:\n      - active\n" in _service_block("docker-compose.yml", "channel_worker")


def test_runtime_compose_starts_core_workers_by_default():
    for name in ("rabbitmq", "heartbeat_worker", "maintenance_worker"):
        assert "\n    profiles:" not in _service_block("ops/docker-compose.runtime.yml", name)

    assert 'command: ["hexis-worker", "--mode", "heartbeat"]' in _service_block(
        "ops/docker-compose.runtime.yml", "heartbeat_worker"
    )
    assert 'command: ["hexis-worker", "--mode", "maintenance"]' in _service_block(
        "ops/docker-compose.runtime.yml", "maintenance_worker"
    )
    assert "\n    profiles:\n      - active\n" in _service_block(
        "ops/docker-compose.runtime.yml", "channel_worker"
    )
