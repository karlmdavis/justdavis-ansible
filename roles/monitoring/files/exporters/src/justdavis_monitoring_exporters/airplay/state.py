"""What a HomePod is doing, read from its `_airplay._tcp` TXT record.

Apple does not document the record. The meanings below were worked out on 2026-09-28 by changing what
nine HomePods played while reading their records every 30 seconds, and checking each reading against
what was audible:

* Bit 20 of `flags` (0x100000) is set while audio plays, whatever the source. It followed every start,
  stop, and pause within one reading for AirPlay from an iPad to a group and to one HomePod, AirPlay
  from a Mac, Apple Music playing on the HomePod, a HomePod following another HomePod, and white noise
  started by a Home scene; it stayed clear for a start that failed.
* `gid` names the group or session the HomePod is in. HomePods playing together share it; a HomePod on
  its own has one to itself. It changes whenever the HomePod joins another session.
* `igl` is 1 when the HomePod leads its group (one on its own leads a group of one), 0 when it follows
  another device: another HomePod, or a phone, tablet, or computer sending AirPlay.
* Pausing or stopping clears bit 20 but leaves the HomePod in its group. One group of seven stayed
  together, silent, for eight minutes after its stream stopped, then re-formed under one of its members.
* One HomePod left a group of eight that kept playing, and went silent. Its record went back to the
  idle form (bit 20 clear, leading a group of one) while it stayed on the WiFi, kept port 7000 open,
  and kept answering queries: the record was the only signal that showed it.

The other bits of `flags` are not interpreted. Bits 11 and 17 were set exactly when `igl` was 0; bit 22
was set for AirPlay from the iPad and clear for AirPlay from the Mac, for a reason not established.
The whole value is exported so that a later reading can be compared with these.
"""

from collections.abc import Mapping

from justdavis_monitoring_exporters.airplay.models import PlaybackState

_AUDIO_PLAYING = 1 << 20


def parse_txt(text: bytes) -> dict[str, str]:
    """The `key=value` fields of a TXT record's wire form (length-prefixed strings)."""
    fields: dict[str, str] = {}
    index = 0
    while index < len(text):
        length = text[index]
        key, _, value = text[index + 1 : index + 1 + length].decode("utf-8", "replace").partition("=")
        fields[key] = value
        index += 1 + length
    return fields


def playback_state(txt: Mapping[str, str]) -> PlaybackState | None:
    """The HomePod's state, or None when the record lacks a field or carries one that cannot be read."""
    try:
        flags = int(txt["flags"], 16)
        group_id = txt["gid"]
        group_leader = {"0": False, "1": True}[txt["igl"]]
    except (KeyError, ValueError):
        return None
    if not group_id:
        return None
    return PlaybackState(
        audio_playing=bool(flags & _AUDIO_PLAYING),
        group_leader=group_leader,
        group_id=group_id,
        status_flags=flags,
    )
