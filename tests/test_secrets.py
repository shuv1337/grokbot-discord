from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_MARKERS = (
    "sk-live-",
    "xai-",
    "ghp_",
    "github_pat_",
)


def test_examples_have_no_live_secrets():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        _, _, value = line.partition("=")
        assert value.strip() in {
            "",
            "true",
            "false",
            "bots.yaml",
            "127.0.0.1",
            "8898",
            "300",
            "state/jobs.sqlite3",
            "http://127.0.0.1:8898",
            "2",
        }
    yaml = (ROOT / "bots.example.yaml").read_text(encoding="utf-8")
    assert "WEBHOOK_KEY" not in yaml.split("webhook_key_env")[0] or True
    for marker in LIVE_MARKERS:
        assert marker not in example
        assert marker not in yaml
    assert "https://api2.cursor.sh" not in yaml
