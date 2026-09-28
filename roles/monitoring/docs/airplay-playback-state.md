# What a HomePod's AirPlay Record Says About Playback: Observations of 2026-09-28

The AirPlay exporter reads whether audio is playing on each HomePod, and which HomePods are grouped,
from the HomePod's `_airplay._tcp` mDNS TXT record.
Apple does not document that record.
This is the record of the session in which its meaning was worked out, kept so that the claims in
`files/exporters/src/justdavis_monitoring_exporters/airplay/state.py` can be checked against what was
actually seen, and re-checked if a software update changes the record.

Device names are replaced (Speaker A to Speaker I), group ids are left out, and times are minutes from
the start of the first stream (T+0).

## Summary

- Bit 20 of the record's `flags` field followed audio for every source tried: it was set while audio
  played and clear otherwise, in 11 changes out of 11, each checked against what was audible or what
  had just been done.
- `gid` named the group or session, and `igl` said whether the HomePod led it.
- The record showed a HomePod dropping out of a playing group when no other signal did.
- The download rate, which the first version of the audio rules used, could not do the job: the router
  reported no download count for seven of the nine HomePods.

## Method

- Nine HomePods, on a WiFi mesh of one router and two mesh points.
- Audio was started, moved, paused, and stopped by hand, from an iPad, a Mac, a phone, and a Home
  scene, and each action's time was noted to the minute.
- Every 30 seconds, a unicast mDNS query for the TXT record of the `_airplay._tcp` instance was sent to
  each HomePod's address from the wired LAN, and the answer recorded.
  That is the query the exporter makes each poll.
- The recording began at T+53.
  Before then there is one reading of the records only, at T+48, which is why the dropout has no exact
  time.
- Traffic rates are from the router's per-client byte counters, as the AmpliFi exporter records them.

## The Fields

| Field | Meaning, as observed |
| ----- | -------------------- |
| `flags` bit 20 (`0x100000`) | Set while audio plays, whatever the source. |
| `flags` bits 11 and 17 (`0x800`, `0x20000`) | Set exactly when `igl` was 0. Never seen apart from each other. |
| `flags` bit 22 (`0x400000`) | Set for AirPlay from the iPad, to eight HomePods and to one. Clear for AirPlay from the Mac. Meaning not established. |
| `gid` | The id of the group or session. HomePods in one group share it; a HomePod on its own has one to itself. A group led by a HomePod, including a HomePod on its own, has two ids joined by `+` (73 characters); a session led by a device sending AirPlay has one (36). |
| `igl` | 1 when the HomePod leads its group, 0 when it follows another device. |
| `gcgl` | 1 when the group's leader is one of its HomePods, 0 when the leader is a device sending AirPlay. Not used by the exporter. |
| `pgid`, `pgcgl` | Present only while `igl` was 0, with the same values as `gid` and `gcgl`. Not used by the exporter. |

## The States Seen

| What the HomePod was doing | `flags` | `igl` | `gcgl` | `gid` |
| -------------------------- | ------- | ----- | ------ | ----- |
| Idle, on its own | `0x98404` | 1 | 1 | Its own. |
| Playing its own stream (Apple Music, or white noise from a Home scene) | `0x198404` | 1 | 1 | Its own. |
| Following another HomePod, playing | `0x1b8c04` | 0 | 1 | The leader's. |
| Following another HomePod, stream stopped | `0xb8c04` | 0 | 1 | The leader's. |
| Following the Mac's AirPlay session, playing | `0x1b8c04` | 0 | 0 | The session's. |
| Following the Mac's AirPlay session, joined but not yet playing, or paused | `0xb8c04` | 0 | 0 | The session's. |
| Following the iPad's AirPlay session, playing | `0x5b8c04` | 0 | 0 | The session's. |
| Following the iPad's AirPlay session, stream stopped | `0x4b8c04` | 0 | 0 | The session's. |
| Leading other HomePods, playing | `0x198404` | 1 | 1 | Its own, shared with its followers. |
| Leading other HomePods, silent | `0x98404` | 1 | 1 | Its own, shared with its followers. |

A HomePod leading a group has the same record as one on its own.
Only its followers' `gid` shows that it has any.

## Timeline

| Time | What was done or heard | What the records showed |
| ---- | ---------------------- | ----------------------- |
| T-7 | Nothing playing. | One reading of the rates only; records not yet read. |
| T+0 | The iPad started an ambient-sound app over AirPlay to eight HomePods (all but Speaker E). Three were heard playing; the other five were not checked. | Not yet read. |
| T+48 | First reading of the records. | Seven following the iPad's session and playing. Speaker B idle and on its own, like Speaker E. |
| T+48 | Speaker B was checked by ear: silent. It had been heard playing at T+0. | As above: the dropout was real. |
| T+53 | Recording every 30 seconds began. | |
| T+69 | Nothing. | Speaker G did not answer one query. Its state was the same before and after. |
| T+72 | Speaker B was added back to the group. | Speaker B following and playing, at the next reading. |
| T+73 | Apple Music was started on Speaker E itself. | Speaker E: bit 20 set, nothing else changed. |
| T+76 | Speaker A was moved from the iPad's stream to Speaker E's. | Speaker A: `gid` became Speaker E's, `gcgl` became 1, bit 22 cleared. Speaker E: no change. |
| T+78 | Both streams were stopped. | Bit 20 cleared on all nine. Every follower stayed in its group. |
| T+81 | The Mac started a music app over AirPlay to Speaker A. | Speaker A took a new `gid` with `gcgl` 0, bit 20 still clear; bit 20 set one reading later. |
| T+83 | The Mac's stream was paused. | Speaker A: bit 20 cleared, still in the session. |
| T+84 | White noise was started on Speaker C, and did not start. | Speaker C: no change. |
| T+86 | Nothing deliberate. A phone's AirPlay chooser was open at about this time. | The iPad's group ended, all in one reading. Speaker C on its own. Speaker D leading a new group of six, all silent. |
| T+87 | A Home scene started white noise on Speaker F. | Speaker F left Speaker D's group: on its own, bit 20 set. |
| T+91 | The white noise was stopped. | Speaker F: bit 20 cleared. |
| T+92 | The iPad started the ambient-sound app over AirPlay to Speaker A only. | Speaker A: new `gid`, bits 20 and 22 set. |
| T+175 | Nothing. | Speakers C and D each missed one query. Speaker C came back with a new `gid`, still on its own. |
| T+208 | Nothing. | Speaker D missed one query; at the next reading its group had dissolved, and each of its five members was on its own. The group had lasted 2 hours 2 minutes, silent throughout. |
| T+297 | Nothing. | Speaker D missed one query, state unchanged. |

Every change that was made by hand showed at the first reading after it.

## The Dropout

Speaker B left a group of eight that kept playing, some time between T+0 and T+48.
Over that whole period it:

- Stayed associated to the same access point, at 5 GHz, with a signal quality of 97 to 100.
- Answered every TCP probe of port 7000.
- Answered every unicast mDNS query.
- Failed the multicast mDNS query from the wired LAN, as it had before the stream started.
- Reported no byte counts in either direction.

So nothing the monitoring recorded at the time changed when it dropped out.
The record was the only signal that did, and it was not being recorded yet.
What caused the dropout is not known.

## Traffic During the First Stream

The router reports a byte count per client and direction.
At T-7, and for at least the four hours before, it reported `4294967295` (2^32 - 1) for many of them,
which the exporter treats as "unavailable":

| Counter | HomePods with a usable count |
| ------- | ---------------------------- |
| Download | Two of nine (Speakers C and D). |
| Upload | Seven of nine (all but Speakers B and E). |

Speakers C and D, with download counts, during the iPad's stream:

| Phase | Download, kbit/s (1-minute rate) |
| ----- | -------------------------------- |
| Idle, the five minutes before | 8 to 10. |
| The first 90 seconds | About 30. |
| Low level | 150 to 260, mostly about 200. |
| High level | 340 to 440, mostly about 400. |
| Mean over 38 minutes | About 290. |

- The stream moved between the two levels 14 times in 38 minutes, in blocks of 1 to 8 minutes.
- The two HomePods stayed within about 3% of each other at every sample.
- Upload on the seven HomePods with a count followed the same levels at the same moments (17 to 40
  kbit/s at the low level, 35 to 80 at the high one), but its low level overlaps idle (8 to 45).

Two readings that a rate threshold would have got wrong:

- At T+82, with Speaker A playing the Mac's stream, it had no download count at all.
- At T+86, Speakers C and D were silent and read 50 and 66 kbit/s, while their group re-formed.

## Multicast Reachability and Playback

Four HomePods failed the multicast mDNS query from the wired LAN at some point during the first stream.
Three of them played on normally; the fourth was Speaker B.
All nine were listed in the iPad's and a phone's AirPlay choosers at T-7.
`HomePodAirPlayNotResolving` therefore does not mean that a HomePod cannot be played to, and failing
that query did not by itself predict the dropout.

## Not Established

- What bit 22 marks. It followed the sending device or app, not the number of HomePods.
- Whether bits 11 and 17 ever differ from each other or from `igl`.
- What ended the iPad's group at T+86, eight minutes after its stream stopped. A silent group lasted
  over two hours later the same day, so a short idle timeout does not explain it.
- Why a HomePod's `gid` changed, or its group dissolved, just after it missed a query (T+175, T+208).
  A brief loss of its network connection would fit, and was not checked.
- Why the router reports a byte count as unavailable. One that was about 350 MB short of 2^32 at T+38
  would show whether a counter sticks once it reaches that value; it was not followed up.
- Whether a HomePod ever sends a TXT record in another form. All nine sent the same set of keys in
  every reading.
- At T-3 a spoken request to set the volume on every HomePod failed on at least three of them. The
  monitoring has no signal for requests between HomePods, so this is recorded and not explained.

## Seen After Deployment

Found from the dashboard on the evening of 2026-09-28, once the exporter was recording the records:

- The two forms of `gid` above. During the session only the first eight characters were kept.
- A scheduled Home automation started a stream on one HomePod with five others following it. Four
  of those five were playing an iPad's stream at the time, which carried on with one HomePod. The
  four were marked as leaving a playing group: the "someone moved it" case, which the record
  cannot tell from a dropout.
- A HomePod whose download count had been unavailable all day had one again after it joined that
  group. Whether it had re-associated to the WiFi was not checked.

## Checking Again

Since this change, Prometheus holds what the session had to collect by hand:

- `airplay_status_flags` is the whole `flags` value, per HomePod.
- `airplay_group_info` carries the `gid` as its `group` label, and `airplay_group_leader` is `igl`.
- `airplay_playback_state_readable` is 0 for a HomePod whose record no longer has the form above, and
  the exporter's log says what it found.

To repeat a check, do something to a HomePod, note the time, and compare those series a minute later
with the tables above.
