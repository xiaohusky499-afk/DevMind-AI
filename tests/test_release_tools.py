from pathlib import Path
from types import SimpleNamespace

from scripts import release
from scripts.release import read_env


def test_read_env_ignores_comments_and_splits_once(tmp_path: Path):
    target = tmp_path / "production.env"
    target.write_text("# comment\nA=one\nURL=https://example.com?a=1\n", encoding="utf-8")

    assert read_env(target) == {"A": "one", "URL": "https://example.com?a=1"}


def test_validate_accepts_complete_secret_set(tmp_path: Path, monkeypatch):
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    for name, minimum in release.SECRET_RULES.items():
        (secret_dir / name).write_text("x" * minimum, encoding="utf-8")
    env_file = tmp_path / "production.env"
    env_file.write_text(
        "DEVMIND_SECRETS_DIR=./secrets\nCORS_ORIGINS=https://devmind.example.com\n",
        encoding="utf-8",
    )
    commands = []
    monkeypatch.setattr(release, "command", lambda *args, **kwargs: commands.append(args))

    environment, resolved = release.validate(
        SimpleNamespace(env_file=str(env_file), dry_run=False)
    )

    assert environment["CORS_ORIGINS"] == "https://devmind.example.com"
    assert resolved == secret_dir
    assert commands
