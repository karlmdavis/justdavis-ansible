"""Tests for `scripts/hue_create_application_key.py`: what it sends, and that the key is never shown."""

import importlib.util
import json
import ssl
import stat
import urllib.error
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[3] / "scripts" / "hue_create_application_key.py"
KEY = "distinctive-test-key-0123456789"
CONFIG = {"bridgeid": "020000FFFE000030", "modelid": "BSB002", "swversion": "1", "apiversion": "1.78.0"}
COMMON_NAME = "020000fffe000030"


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("hue_create_application_key", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    return module


def bridge(script: ModuleType, monkeypatch: pytest.MonkeyPatch, answers: list[object]) -> list[bytes | None]:
    """Serve the public configuration, then `answers` (an exception is raised) to the key requests."""
    posted: list[bytes | None] = []

    def fetch(url: str, _context: ssl.SSLContext, data: bytes | None = None) -> tuple[object, str]:
        if url.endswith("/api/0/config"):
            return CONFIG, COMMON_NAME
        posted.append(data)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, COMMON_NAME

    monkeypatch.setattr(script, "fetch", fetch)
    return posted


def test_creates_the_key_after_the_button_and_writes_it_unshown_and_private(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    posted = bridge(
        script, monkeypatch, [[{"error": {"type": 101}}], [{"success": {"username": KEY, "clientkey": "x"}}]]
    )
    out = tmp_path / "key.txt"
    assert script.main(["--bridge", "192.0.2.21", "--out", str(out)]) == 0
    # The request names the key and asks for nothing else (the bridge rejects `generateclientkey: false`).
    assert [json.loads(body or b"") for body in posted] == [
        {"devicetype": "justdavis-monitoring#eddings"}
    ] * 2
    assert out.read_text() == KEY + "\n"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    shown = capsys.readouterr()
    assert KEY not in shown.out + shown.err
    assert "link button not pressed (attempt 1 of 6)" in shown.out


def test_check_only_verifies_and_asks_for_no_key(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    posted = bridge(script, monkeypatch, [])
    out = tmp_path / "key.txt"
    assert script.main(["--bridge", "192.0.2.21", "--out", str(out), "--check"]) == 0
    assert (posted, out.exists()) == ([], False)


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        ([{"error": {"type": 7, "description": "invalid value, secret detail"}}], "error type 7"),
        ([{"success": {}}], "without a key"),
        (urllib.error.URLError(TimeoutError("timed out")), "could not reach the bridge"),
        (urllib.error.URLError(ssl.SSLCertVerificationError("self-signed certificate")), "TLS: the bridge's"),
        (json.JSONDecodeError("Expecting value", "<html>", 0), "is not JSON"),
    ],
    ids=["refused", "no key", "timeout", "certificate", "not json"],
)
def test_a_failed_key_request_says_why_shows_no_answer_and_leaves_no_file(
    script: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    answer: object,
    said: str,
) -> None:
    bridge(script, monkeypatch, [answer])
    out = tmp_path / "key.txt"
    assert script.main(["--bridge", "192.0.2.21", "--out", str(out)]) == 1
    shown = capsys.readouterr()
    assert said in shown.err
    assert "secret detail" not in shown.out + shown.err
    assert not out.exists()


def test_gives_up_when_the_button_is_never_pressed(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    posted = bridge(script, monkeypatch, [[{"error": {"type": 101}}] for _ in range(6)])
    assert script.main(["--bridge", "192.0.2.21", "--out", str(tmp_path / "key.txt")]) == 1
    assert len(posted) == 6
    assert "gave up" in capsys.readouterr().err


def test_an_unwritable_or_existing_output_path_is_refused_before_any_key_is_requested(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    posted = bridge(script, monkeypatch, [[{"success": {"username": KEY}}]])
    existing = tmp_path / "key.txt"
    existing.write_text("earlier")
    assert script.main(["--bridge", "192.0.2.21", "--out", str(existing)]) == 1
    assert script.main(["--bridge", "192.0.2.21", "--out", str(tmp_path / "missing" / "key.txt")]) == 1
    assert (posted, existing.read_text()) == ([], "earlier")
    assert "already exists" in capsys.readouterr().err


def test_a_certificate_naming_another_device_or_an_unexpected_bridge_is_refused(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    posted = bridge(script, monkeypatch, [])
    out = str(tmp_path / "key.txt")
    assert script.main(["--bridge", "192.0.2.21", "--out", out, "--bridge-id", "020000fffe000099"]) == 1
    monkeypatch.setattr(script, "fetch", lambda *_a, **_k: ({"modelid": "BSB002"}, ""))
    # No bridge id and no common name must not pass as "they match".
    assert script.main(["--bridge", "192.0.2.21", "--out", out]) == 1
    assert posted == []
    assert "not the bridge id" in capsys.readouterr().err


def test_a_failed_first_request_tells_tls_from_unreachable(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> tuple[object, str]:
        raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))

    monkeypatch.setattr(script, "fetch", refuse)
    assert script.main(["--bridge", "192.0.2.21", "--check"]) == 1
    assert "does not validate against Signify's root" in capsys.readouterr().err
