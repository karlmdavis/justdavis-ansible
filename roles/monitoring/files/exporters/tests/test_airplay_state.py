"""Tests for reading a HomePod's playback state from its AirPlay TXT record."""

import pytest

from justdavis_monitoring_exporters.airplay.models import PlaybackState
from justdavis_monitoring_exporters.airplay.state import parse_txt, playback_state

GROUP = "00000000-0000-4000-8000-0000000000F0"


def wire(*fields: str) -> bytes:
    return b"".join(bytes([len(field.encode())]) + field.encode() for field in fields)


def test_parse_txt_splits_length_prefixed_fields_at_the_first_equals_sign() -> None:
    assert parse_txt(wire("flags=0x98404", "igl=1", "pk=a=b", "bare")) == {
        "flags": "0x98404",
        "igl": "1",
        "pk": "a=b",
        "bare": "",
    }
    assert parse_txt(b"") == {}


def test_parse_txt_tolerates_a_field_cut_short_and_bytes_that_are_not_text() -> None:
    assert parse_txt(b"\x09igl=1") == {"igl": "1"}
    assert parse_txt(wire("igl=1") + b"\x02\xff\xfe") == {"igl": "1", "��": ""}


# The flag values are the ones read from the HomePods on 2026-09-28.
@pytest.mark.parametrize(
    ("flags", "leader", "playing"),
    [
        pytest.param("0x98404", "1", False, id="idle-on-its-own"),
        pytest.param("0x198404", "1", True, id="playing-its-own-stream"),
        pytest.param("0x1b8c04", "0", True, id="following-a-homepod-or-a-mac"),
        pytest.param("0x5b8c04", "0", True, id="following-an-ipad"),
        pytest.param("0xb8c04", "0", False, id="following-with-the-stream-paused"),
        pytest.param("0x4b8c04", "0", False, id="following-an-ipad-with-the-stream-stopped"),
    ],
)
def test_audio_playing_follows_bit_20_whatever_the_other_bits_say(
    flags: str, leader: str, playing: bool
) -> None:
    assert playback_state({"flags": flags, "gid": GROUP, "igl": leader}) == PlaybackState(
        audio_playing=playing, group_leader=leader == "1", group_id=GROUP, status_flags=int(flags, 16)
    )


@pytest.mark.parametrize(
    "txt",
    [
        pytest.param({"gid": GROUP, "igl": "1"}, id="no-flags"),
        pytest.param({"flags": "0x98404", "igl": "1"}, id="no-group-id"),
        pytest.param({"flags": "0x98404", "gid": GROUP}, id="no-leader-field"),
        pytest.param({"flags": "many", "gid": GROUP, "igl": "1"}, id="flags-not-a-number"),
        pytest.param({"flags": "0x98404", "gid": "", "igl": "1"}, id="empty-group-id"),
        pytest.param({"flags": "0x98404", "gid": GROUP, "igl": "yes"}, id="leader-field-not-0-or-1"),
    ],
)
def test_a_record_that_cannot_be_read_in_full_gives_no_state(txt: dict[str, str]) -> None:
    assert playback_state(txt) is None
