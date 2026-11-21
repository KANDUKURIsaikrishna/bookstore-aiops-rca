import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chaos_drill  # noqa: E402

REQUIRED_LABELS = {"fault-service", "category", "root-cause", "expected-alerts", "duration"}


def test_parse_duration_units():
    assert chaos_drill.parse_duration("90s") == 90
    assert chaos_drill.parse_duration("6m") == 360
    assert chaos_drill.parse_duration("1h") == 3600
    assert chaos_drill.parse_duration("45") == 45


@pytest.mark.parametrize("bad", ["", "6 minutes", "-1m", "1.5m", "m"])
def test_parse_duration_rejects_garbage(bad):
    with pytest.raises(ValueError):
        chaos_drill.parse_duration(bad)


def test_parse_annotations_strips_quotes_and_ignores_others():
    manifest = """
metadata:
  annotations:
    drill.bookstore.io/root-cause: "pod killed; 502s"
    drill.bookstore.io/duration: 3m
    other.io/thing: "x"
"""
    assert chaos_drill.parse_annotations(manifest) == {"root-cause": "pod killed; 502s", "duration": "3m"}


def test_substitute_replaces_and_reports_missing():
    assert chaos_drill.substitute("host: ${RDS_ENDPOINT}", {"RDS_ENDPOINT": "db.local"}.get) == "host: db.local"
    with pytest.raises(ValueError, match="RDS_ENDPOINT"):
        chaos_drill.substitute("host: ${RDS_ENDPOINT}", lambda _n: None)


@pytest.mark.parametrize("path", list(chaos_drill.all_experiments()), ids=lambda p: p.stem)
def test_every_experiment_is_fully_labeled(path):
    text = path.read_text()
    labels = chaos_drill.parse_annotations(text)
    missing = REQUIRED_LABELS - labels.keys()
    assert not missing, f"{path.name} missing {missing}"
    chaos_drill.parse_duration(labels["duration"])
    assert chaos_drill.manifest_field(text, "namespace"), f"{path.name} has no namespace"


def test_experiments_only_target_opted_in_namespaces():
    allowed = set(chaos_drill.SERVICE_NAMESPACES.values()) | {"bookstore", "chaos-testing"}
    for path in chaos_drill.all_experiments():
        ns = chaos_drill.manifest_field(path.read_text(), "namespace")
        assert ns in allowed, f"{path.name} targets {ns}, not an opted-in namespace"


def test_dry_run_records_ground_truth(monkeypatch, capsys):
    monkeypatch.setenv("RDS_ENDPOINT", "db.example")
    monkeypatch.setattr(chaos_drill, "try_capture", lambda *a, **k: "")
    chaos_drill.main(["run", "pod-kill-catalog", "--dry-run", "--notes", "unit"])
    out = capsys.readouterr().out
    assert "[dry-run] kubectl apply -f -" in out
    assert "[dry-run] kubectl delete -f - --ignore-not-found" in out
    assert '"fault_service": "catalog-service"' in out
    assert '"notes": "unit"' in out
