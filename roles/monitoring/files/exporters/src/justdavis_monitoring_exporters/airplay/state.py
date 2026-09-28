"""What a HomePod is doing, read from its `_airplay._tcp` TXT record.

Apple does not document the record. The meanings below were worked out on 2026-09-28 by changing what
nine HomePods played while reading their records every 30 seconds, and checking each reading against
what was audible:

* Bit 20 of `flags` (0x100000) is set while audio plays, whatever the source. It followed every start,
  stop, and pause at the next reading for AirPlay from an iPad to a group and to one HomePod, AirPlay
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

Because none of this is documented, a software update could change it. A record that does not have the
form seen here is reported as unreadable, with the reason, and never guessed at: a HomePod wrongly
shown as silent or as alone in its group is worse than one shown as unknown.
"""

import re
from collections.abc import Mapping

from justdavis_monitoring_exporters.airplay.models import PlaybackState
from justdavis_monitoring_exporters.common.labels import sanitise_label

_AUDIO_PLAYING = 1 << 20
# The form seen on every HomePod: "0x" and up to sixteen hexadecimal digits. Python's own int() would
# also take a sign, underscores, and surrounding spaces, and a negative number has every high bit set.
_FLAGS = re.compile(r"0[xX][0-9a-fA-F]{1,16}")


class UnreadableRecord(ValueError):
    """The record does not have the form the playback state is read from; the message says how."""


def parse_txt(text: bytes) -> dict[str, str]:
    """The `key=value` fields of a TXT record's wire form (length-prefixed strings). A field whose
    length runs past the end of the record is cut short, so it and anything after it are left out:
    half a value must not be read as a whole one."""
    fields: dict[str, str] = {}
    index = 0
    while index < len(text):
        length = text[index]
        field = text[index + 1 : index + 1 + length]
        if len(field) < length:
            break
        key, _, value = field.decode("utf-8", "replace").partition("=")
        fields[key] = value
        index += 1 + length
    return fields


def playback_state(txt: Mapping[str, str] | None) -> PlaybackState:
    """The HomePod's state; `txt` is None when its answer carried no TXT record."""
    if txt is None:
        raise UnreadableRecord("the answer carried no TXT record")
    missing = [key for key in ("flags", "gid", "igl") if key not in txt]
    if missing:
        raise UnreadableRecord(f"the record has no {', '.join(missing)} (it has {', '.join(sorted(txt))})")
    if not _FLAGS.fullmatch(txt["flags"]):
        raise UnreadableRecord(f"flags is {txt['flags'][:32]!r}, not a hexadecimal number")
    if txt["igl"] not in ("0", "1"):
        raise UnreadableRecord(f"igl is {txt['igl'][:32]!r}, not 0 or 1")
    # Sanitised here, not where it is exported, so that an id with nothing printable in it is caught:
    # an empty label value is no label at all, and every such HomePod would seem to share a group.
    group_id = sanitise_label(txt["gid"])
    if not group_id:
        raise UnreadableRecord("gid is empty")
    flags = int(txt["flags"], 16)
    return PlaybackState(
        audio_playing=bool(flags & _AUDIO_PLAYING),
        group_leader=txt["igl"] == "1",
        group_id=group_id,
        status_flags=flags,
    )
