#!/usr/bin/env python3
"""Deterministic builders for MIDI/MusicXML import fixtures.

Run from the backend directory:

    ../.venv/bin/python -m tests.fixtures.build_import_fixtures

Writes binary fixtures under ``tests/fixtures/import/`` plus ``manifest.json``
(SHA-256 digests) and ``expected_vectors.json`` (playable semantic tuples).
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import mido

from app.services.composition_midi_import import import_midi_bytes
from app.services.composition_musicxml_import import import_musicxml_bytes


FIXTURE_DIR = Path(__file__).resolve().parent / "import"

# Sentinel strings must never appear in sanitized logs/error details (Task 10).
SENTINEL_FILENAME = "SECRET_IMPORT_FIXTURE_NAME.mid"
SENTINEL_LYRIC = "SECRET_IMPORT_LYRIC_TOKEN"
SENTINEL_TITLE = "SECRET_IMPORT_TITLE_TOKEN"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _midi_bytes(mid: mido.MidiFile) -> bytes:
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def build_multitrack_midi() -> bytes:
    """Type-1 multi-track MIDI with tempo/meter/key, polyphony, CC, sustain, drums."""
    mid = mido.MidiFile(type=1, ticks_per_beat=480)

    conductor = mido.MidiTrack()
    mid.tracks.append(conductor)
    conductor.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100), time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    conductor.append(mido.MetaMessage("key_signature", key="C", time=0))
    conductor.append(mido.MetaMessage("marker", text="A", time=0))
    # Bar 2 (tick 1920): meter 3/4, tempo 120, key G
    conductor.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=1920))
    conductor.append(mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0))
    conductor.append(mido.MetaMessage("key_signature", key="G", time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=0))

    piano = mido.MidiTrack()
    mid.tracks.append(piano)
    piano.append(mido.MetaMessage("track_name", name="Piano", time=0))
    piano.append(mido.Message("program_change", program=0, channel=0, time=0))
    piano.append(mido.Message("control_change", control=7, value=100, channel=0, time=0))
    piano.append(mido.Message("control_change", control=10, value=64, channel=0, time=0))
    piano.append(mido.Message("control_change", control=11, value=127, channel=0, time=0))
    piano.append(mido.Message("control_change", control=64, value=127, channel=0, time=0))
    # Polyphonic chord at tick 0
    piano.append(mido.Message("note_on", note=60, velocity=90, channel=0, time=0))
    piano.append(mido.Message("note_on", note=64, velocity=80, channel=0, time=0))
    piano.append(mido.Message("note_off", note=60, velocity=0, channel=0, time=480))
    piano.append(mido.Message("note_off", note=64, velocity=0, channel=0, time=0))
    piano.append(mido.Message("control_change", control=11, value=90, channel=0, time=480))
    piano.append(mido.Message("control_change", control=64, value=0, channel=0, time=480))
    piano.append(mido.Message("note_on", note=67, velocity=95, channel=0, time=0))
    piano.append(mido.Message("note_off", note=67, velocity=0, channel=0, time=480))
    # Continue into the 3/4 section that begins at tick 1920.
    piano.append(mido.Message("note_on", note=72, velocity=88, channel=0, time=480))
    piano.append(mido.Message("note_off", note=72, velocity=0, channel=0, time=480))
    piano.append(mido.MetaMessage("end_of_track", time=0))

    bass = mido.MidiTrack()
    mid.tracks.append(bass)
    bass.append(mido.MetaMessage("track_name", name="Acoustic Bass", time=0))
    bass.append(mido.Message("program_change", program=32, channel=1, time=0))
    bass.append(mido.Message("note_on", note=36, velocity=100, channel=1, time=0))
    bass.append(mido.Message("note_off", note=36, velocity=0, channel=1, time=960))
    bass.append(mido.MetaMessage("end_of_track", time=0))

    drums = mido.MidiTrack()
    mid.tracks.append(drums)
    drums.append(mido.MetaMessage("track_name", name="Drums", time=0))
    drums.append(mido.Message("note_on", note=36, velocity=110, channel=9, time=0))
    drums.append(mido.Message("note_off", note=36, velocity=0, channel=9, time=240))
    drums.append(mido.Message("note_on", note=38, velocity=100, channel=9, time=240))
    drums.append(mido.Message("note_off", note=38, velocity=0, channel=9, time=240))
    drums.append(mido.MetaMessage("end_of_track", time=0))

    return _midi_bytes(mid)


MULTIPART_MUSICXML = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <work><work-title>Import Fixture</work-title></work>
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
    <score-part id="P2"><part-name>Violin</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <direction placement="above">
        <direction-type><dynamics><mf/></dynamics></direction-type>
        <sound dynamics="90"/>
      </direction>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>4</duration>
        <tie type="start"/>
        <voice>1</voice>
        <type>whole</type>
        <notations>
          <tied type="start"/>
          <articulations><accent/></articulations>
        </notations>
      </note>
    </measure>
    <measure number="2">
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>2</duration>
        <tie type="stop"/>
        <voice>1</voice>
        <type>half</type>
        <notations><tied type="stop"/></notations>
      </note>
      <note>
        <pitch><step>E</step><octave>4</octave></pitch>
        <duration>1</duration>
        <voice>1</voice>
        <type>quarter</type>
        <notations><articulations><staccato/></articulations></notations>
      </note>
      <note>
        <chord/>
        <pitch><step>G</step><octave>4</octave></pitch>
        <duration>1</duration>
        <voice>1</voice>
        <type>quarter</type>
      </note>
      <note>
        <rest/>
        <duration>1</duration>
        <voice>1</voice>
        <type>quarter</type>
      </note>
    </measure>
  </part>
  <part id="P2">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note>
        <pitch><step>G</step><octave>5</octave></pitch>
        <duration>4</duration>
        <voice>1</voice>
        <type>whole</type>
      </note>
    </measure>
    <measure number="2">
      <note>
        <pitch><step>A</step><octave>5</octave></pitch>
        <duration>4</duration>
        <voice>1</voice>
        <type>whole</type>
      </note>
    </measure>
  </part>
</score-partwise>
"""


def build_multipart_musicxml() -> bytes:
    return MULTIPART_MUSICXML.encode("utf-8")


def build_mxl(xml_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        container = zipfile.ZipInfo("META-INF/container.xml")
        container.date_time = (2026, 1, 1, 0, 0, 0)
        container.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(
            container,
            """<?xml version="1.0" encoding="UTF-8"?>
<container>
  <rootfiles>
    <rootfile full-path="score.xml"/>
  </rootfiles>
</container>
""",
        )
        score = zipfile.ZipInfo("score.xml")
        score.date_time = (2026, 1, 1, 0, 0, 0)
        score.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(score, xml_bytes.decode("utf-8"))
    return buf.getvalue()


def build_truncated_midi() -> bytes:
    data = build_multitrack_midi()
    return data[:24]


def build_empty() -> bytes:
    return b""


def build_entity_musicxml() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE score-partwise [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<score-partwise><part-list></part-list></score-partwise>"
    ).encode("utf-8")


def build_zip_slip_mxl() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        evil = zipfile.ZipInfo("../evil.xml")
        evil.date_time = (2026, 1, 1, 0, 0, 0)
        evil.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(evil, MULTIPART_MUSICXML)
        container = zipfile.ZipInfo("META-INF/container.xml")
        container.date_time = (2026, 1, 1, 0, 0, 0)
        container.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(
            container,
            """<?xml version="1.0"?><container><rootfiles>
            <rootfile full-path="../evil.xml"/></rootfiles></container>""",
        )
    return buf.getvalue()


def _note_vector(composition) -> list[dict]:
    rows: list[dict] = []
    for track in composition.tracks:
        for event in track.events:
            rows.append(
                {
                    "track_id": track.id,
                    "pitch": event.pitch,
                    "start_tick": event.start_tick,
                    "duration_ticks": event.duration_ticks,
                    "velocity": event.velocity,
                    "event_id": event.id,
                    "articulations": list(event.articulations or []),
                    "tie": event.tie.model_dump(mode="json") if event.tie else None,
                }
            )
    return sorted(
        rows,
        key=lambda row: (row["track_id"], row["start_tick"], row["pitch"], row["duration_ticks"]),
    )


def _meta_vector(composition) -> dict:
    return {
        "schema_version": composition.schema_version,
        "tempo": composition.tempo,
        "key": composition.key,
        "time_signature": composition.time_signature,
        "ticks_per_quarter": composition.ticks_per_quarter,
        "bar_count": composition.bar_count,
        "duration_ticks": composition.duration_ticks,
        "harmony": composition.harmony,
        "section_types": [section.type for section in composition.sections],
        "tempo_changes": [
            {"tick": change.tick, "bpm": change.bpm} for change in composition.tempo_changes
        ],
        "time_signature_changes": [
            {"tick": change.tick, "time_signature": change.time_signature}
            for change in composition.time_signature_changes
        ],
        "key_changes": [
            {"tick": change.tick, "key": change.key} for change in composition.key_changes
        ],
        "tracks": [
            {
                "id": track.id,
                "name": track.name,
                "instrument": track.instrument,
                "role": track.role,
                "midi_program": track.midi_program,
                "channel": track.channel,
                "is_drum": track.is_drum,
                "volume": track.volume,
                "pan": track.pan,
                "expression": track.expression,
                "event_count": len(track.events),
                "dynamic_mark_count": len(track.dynamic_marks or []),
                "sustain_count": len(track.sustain_pedals or []),
                "automation_count": len(track.automation or []),
            }
            for track in composition.tracks
        ],
    }


def _issue_codes(report) -> list[str]:
    return sorted({issue.code for issue in report.issues})


def write_all() -> dict[str, str]:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    midi = build_multitrack_midi()
    musicxml = build_multipart_musicxml()
    mxl = build_mxl(musicxml)
    files = {
        "multitrack.mid": midi,
        "multipart.musicxml": musicxml,
        "multipart.mxl": mxl,
        "truncated.mid": build_truncated_midi(),
        "empty.bin": build_empty(),
        "entities.musicxml": build_entity_musicxml(),
        "zip_slip.mxl": build_zip_slip_mxl(),
    }
    # Sentinel-named copy used only by secret-hygiene tests (Task 10).
    files[SENTINEL_FILENAME] = midi

    digests: dict[str, str] = {}
    for name, data in files.items():
        path = FIXTURE_DIR / name
        path.write_bytes(data)
        digests[name] = _sha256(data)

    midi_result = import_midi_bytes(midi, display_filename="multitrack.mid")
    xml_result = import_musicxml_bytes(musicxml, display_filename="multipart.musicxml")
    mxl_result = import_musicxml_bytes(mxl, display_filename="multipart.mxl")

    vectors = {
        "sentinels": {
            "filename": SENTINEL_FILENAME,
            "lyric": SENTINEL_LYRIC,
            "title": SENTINEL_TITLE,
        },
        "multitrack.mid": {
            "issue_codes": _issue_codes(midi_result.import_report),
            "meta": _meta_vector(midi_result.composition),
            "notes": _note_vector(midi_result.composition),
        },
        "multipart.musicxml": {
            "issue_codes": _issue_codes(xml_result.import_report),
            "meta": _meta_vector(xml_result.composition),
            "notes": _note_vector(xml_result.composition),
        },
        "multipart.mxl": {
            "issue_codes": _issue_codes(mxl_result.import_report),
            "meta": _meta_vector(mxl_result.composition),
            "notes": _note_vector(mxl_result.composition),
        },
    }

    (FIXTURE_DIR / "manifest.json").write_text(
        json.dumps({"sha256": digests}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (FIXTURE_DIR / "expected_vectors.json").write_text(
        json.dumps(vectors, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return digests


def assert_fixture_parity() -> None:
    """Fail if committed fixture bytes drift from the deterministic builder."""
    digests = {}
    midi = build_multitrack_midi()
    musicxml = build_multipart_musicxml()
    digests["multitrack.mid"] = _sha256(midi)
    digests["multipart.musicxml"] = _sha256(musicxml)
    digests["multipart.mxl"] = _sha256(build_mxl(musicxml))
    digests["truncated.mid"] = _sha256(build_truncated_midi())
    digests["empty.bin"] = _sha256(build_empty())
    digests["entities.musicxml"] = _sha256(build_entity_musicxml())
    digests["zip_slip.mxl"] = _sha256(build_zip_slip_mxl())
    digests[SENTINEL_FILENAME] = digests["multitrack.mid"]

    manifest_path = FIXTURE_DIR / "manifest.json"
    if not manifest_path.exists():
        raise AssertionError("import fixture manifest.json is missing; run build_import_fixtures")
    stored = json.loads(manifest_path.read_text(encoding="utf-8"))["sha256"]
    for name, digest in digests.items():
        if stored.get(name) != digest:
            raise AssertionError(
                f"import fixture digest drift for {name}: expected {digest}, stored {stored.get(name)}"
            )
        on_disk = FIXTURE_DIR / name
        if not on_disk.exists():
            raise AssertionError(f"missing import fixture file: {name}")
        if _sha256(on_disk.read_bytes()) != digest:
            raise AssertionError(f"on-disk import fixture bytes drifted for {name}")


if __name__ == "__main__":
    written = write_all()
    print(json.dumps({"wrote": sorted(written.keys()), "count": len(written)}, indent=2))
