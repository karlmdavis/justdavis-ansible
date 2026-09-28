"""Tests for reading a HomePod's playback state from its AirPlay TXT record."""

import pytest

from justdavis_monitoring_exporters.airplay.models import PlaybackState
from justdavis_monitoring_exporters.airplay.state import UnreadableRecord, parse_txt, playback_state

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


def test_parse_txt_leaves_out_a_field_cut_short_so_half_a_value_is_never_read_as_whole() -> None:
    # "flags=0x198404" (playing) cut after "0x1984" would read as a number with bit 20 clear.
    cut = wire("igl=1", "flags=0x198404")[:-2]
    assert parse_txt(cut) == {"igl": "1"}
    with pytest.raises(UnreadableRecord, match="no flags"):
        playback_state({**parse_txt(cut), "gid": GROUP})


def test_parse_txt_does_not_fail_on_bytes_that_are_not_text() -> None:
    assert parse_txt(wire("igl=1") + b"\x02\xff\xfe")["igl"] == "1"


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


def test_group_id_of_a_homepod_led_group_is_kept_whole_and_a_longer_one_is_capped() -> None:
    led_by_a_homepod = f"{GROUP}+00000000-0000-4000-8000-0000000000F1"
    assert len(led_by_a_homepod) == 73
    state = playback_state({"flags": "0x98404", "gid": led_by_a_homepod, "igl": "1"})
    assert state.group_id == led_by_a_homepod
    capped = playback_state({"flags": "0x98404", "gid": "g" * 500, "igl": "1"})
    assert capped.group_id == "g" * 128


@pytest.mark.parametrize(
    ("txt", "reason"),
    [
        pytest.param(None, "no TXT record", id="no-record"),
        pytest.param({"gid": GROUP, "igl": "1"}, "no flags", id="no-flags"),
        pytest.param({"flags": "0x98404", "igl": "1"}, "no gid", id="no-group-id"),
        pytest.param({"flags": "0x98404", "gid": GROUP}, "no igl", id="no-leader-field"),
        pytest.param({"model": "Example1,1"}, "no flags, gid, igl", id="another-kind-of-record"),
        pytest.param({"flags": "many", "gid": GROUP, "igl": "1"}, "flags is 'many'", id="flags-a-word"),
        # Python's int() would take each of these; the first would read as "audio playing".
        pytest.param({"flags": "-0x4", "gid": GROUP, "igl": "1"}, "flags is", id="flags-negative"),
        pytest.param({"flags": " 0x98404", "gid": GROUP, "igl": "1"}, "flags is", id="flags-spaced"),
        pytest.param({"flags": "0x9_8404", "gid": GROUP, "igl": "1"}, "flags is", id="flags-underscore"),
        pytest.param({"flags": "98404", "gid": GROUP, "igl": "1"}, "flags is", id="flags-no-prefix"),
        pytest.param({"flags": "0x4,0x8", "gid": GROUP, "igl": "1"}, "flags is", id="flags-two-words"),
        pytest.param({"flags": "0x98404", "gid": "", "igl": "1"}, "gid is empty", id="empty-group-id"),
        pytest.param(
            {"flags": "0x98404", "gid": "éé", "igl": "1"}, "gid is empty", id="group-id-unprintable"
        ),
        pytest.param({"flags": "0x98404", "gid": GROUP, "igl": "yes"}, "igl is 'yes'", id="leader-a-word"),
    ],
)
def test_a_record_not_in_the_form_seen_is_unreadable_and_says_why(
    txt: dict[str, str] | None, reason: str
) -> None:
    with pytest.raises(UnreadableRecord, match=reason):
        playback_state(txt)


def test_the_reason_names_the_fields_the_record_does_have() -> None:
    with pytest.raises(UnreadableRecord, match=r"no igl \(it has flags, gid, model\)"):
        playback_state({"flags": "0x98404", "gid": GROUP, "model": "Example1,1"})
