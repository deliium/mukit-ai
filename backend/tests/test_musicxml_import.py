"""Tests for hardened MusicXML / MXL import."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.import_schemas import CompositionImportError
from app.import_settings import load_import_settings
from app.services.composition_musicxml_import import import_musicxml_bytes, parse_musicxml_bytes
from app.services.composition_timeline import compile_timeline


MINIMAL_PARTWISE = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list>
    <score-part id="P1">
      <part-name>Piano</part-name>
    </score-part>
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
        <direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>100</per-minute></metronome></direction-type>
        <sound tempo="100"/>
      </direction>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>1</duration>
        <voice>1</voice>
        <type>quarter</type>
        <notations>
          <articulations><staccato/></articulations>
        </notations>
      </note>
      <note>
        <pitch><step>E</step><octave>4</octave></pitch>
        <duration>1</duration>
        <voice>1</voice>
        <type>quarter</type>
      </note>
      <note>
        <pitch><step>G</step><octave>4</octave></pitch>
        <duration>1</duration>
        <voice>1</voice>
        <type>quarter</type>
      </note>
      <note>
        <pitch><step>C</step><octave>5</octave></pitch>
        <duration>1</duration>
        <voice>1</voice>
        <type>quarter</type>
      </note>
    </measure>
  </part>
</score-partwise>
"""


def _mxl_bytes(xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container>
  <rootfiles>
    <rootfile full-path="score.xml"/>
  </rootfiles>
</container>
""",
        )
        archive.writestr("score.xml", xml)
    return buf.getvalue()


def test_import_minimal_musicxml_to_v2():
    result = import_musicxml_bytes(MINIMAL_PARTWISE.encode("utf-8"), display_filename="min.musicxml")
    composition = result.composition
    assert composition.schema_version == "composition.v2"
    assert composition.harmony == []
    assert composition.sections[0].type == "unsectioned"
    assert len(composition.tracks) == 1
    assert len(composition.tracks[0].events) == 4
    assert composition.tracks[0].events[0].pitch.startswith("C")
    assert "staccato" in composition.tracks[0].events[0].articulations
    compile_timeline(composition)


def test_musicxml_import_is_deterministic():
    data = MINIMAL_PARTWISE.encode("utf-8")
    first = import_musicxml_bytes(data, display_filename="a.musicxml")
    second = import_musicxml_bytes(data, display_filename="b.musicxml")
    assert first.composition.model_dump(mode="json") == second.composition.model_dump(mode="json")


def test_mxl_import_succeeds():
    result = import_musicxml_bytes(_mxl_bytes(MINIMAL_PARTWISE), display_filename="pack.mxl")
    assert result.import_report.summary.detected_format == "mxl"
    assert len(result.composition.tracks[0].events) == 4


def test_reject_xml_entities():
    payload = MINIMAL_PARTWISE.replace(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM \"file:///etc/passwd\">]>",
    )
    with pytest.raises(CompositionImportError) as exc_info:
        parse_musicxml_bytes(payload.encode("utf-8"))
    assert exc_info.value.http_status == 422
    assert exc_info.value.code == "import_malformed_source"


def test_reject_external_dtd():
    payload = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
        '"http://www.musicxml.org/dtds/partwise.dtd">\n'
        + MINIMAL_PARTWISE.split("?>", 1)[1]
    )
    with pytest.raises(CompositionImportError) as exc_info:
        parse_musicxml_bytes(payload.encode("utf-8"))
    assert exc_info.value.code == "import_malformed_source"


def test_reject_zip_slip_mxl():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("../evil.xml", MINIMAL_PARTWISE)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?><container><rootfiles>
            <rootfile full-path="../evil.xml"/></rootfiles></container>""",
        )
    with pytest.raises(CompositionImportError) as exc_info:
        parse_musicxml_bytes(buf.getvalue())
    assert exc_info.value.http_status == 422


def test_reject_nested_archive_in_mxl():
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as nested:
        nested.writestr("x.xml", MINIMAL_PARTWISE)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("nested.mxl", inner.getvalue())
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?><container><rootfiles>
            <rootfile full-path="score.xml"/></rootfiles></container>""",
        )
        archive.writestr("score.xml", MINIMAL_PARTWISE)
    with pytest.raises(CompositionImportError) as exc_info:
        parse_musicxml_bytes(buf.getvalue())
    assert "nested" in str(exc_info.value.details.get("reason", "")).lower() or exc_info.value.http_status == 422


def test_reject_empty_and_non_xml():
    with pytest.raises(CompositionImportError) as empty:
        parse_musicxml_bytes(b"")
    assert empty.value.http_status == 422
    with pytest.raises(CompositionImportError) as bad:
        parse_musicxml_bytes(b"MThd\x00\x00")
    assert bad.value.http_status == 415


def test_archive_entry_limit():
    settings = load_import_settings({"IMPORT_MAX_ARCHIVE_ENTRIES": "1"})
    with pytest.raises(CompositionImportError) as exc_info:
        parse_musicxml_bytes(_mxl_bytes(MINIMAL_PARTWISE), settings=settings)
    assert exc_info.value.http_status == 413
