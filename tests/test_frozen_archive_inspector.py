"""Synthetic archive checks: no real EXE, app imports, code execution or device IO."""
import builtins
import hashlib
import importlib.util
import marshal
import struct
import zlib

import pytest

from tools import inspect_frozen_archive as inspector


def archive_bytes(payload=b"data", records=None, **cookie_overrides):
    """Build just the documented archive records, not a runnable executable."""
    if records is None:
        records = [(b"sample\0", 0, len(payload), len(payload), 0, b"x")]
    toc = b"".join(
        inspector.TOC_ENTRY.pack(
            inspector.TOC_ENTRY.size + len(name), offset, packed, raw, compressed, kind,
        ) + name
        for name, offset, packed, raw, compressed, kind in records
    )
    fields = dict(
        package_size=len(payload) + len(toc) + inspector.COOKIE.size,
        toc_offset=len(payload), toc_size=len(toc), version=311,
    )
    fields.update(cookie_overrides)
    cookie = inspector.COOKIE.pack(
        inspector.COOKIE_MAGIC, fields["package_size"], fields["toc_offset"],
        fields["toc_size"], fields["version"], b"python311.dll".ljust(64, b"\0"),
    )
    return b"synthetic non-executable prefix" + payload + toc + cookie


def write_archive(tmp_path, content):
    path = tmp_path / "synthetic.pkg"
    path.write_bytes(content)
    return path


def pyz_bytes(payload=None, toc=None, marshal_version=4, toc_bytes=None):
    if payload is None:
        payload = zlib.compress(b"not application bytecode")
    if toc is None:
        toc = [("synthetic.module", (0, 12, len(payload)))]
    if toc_bytes is None:
        toc_bytes = marshal.dumps(toc, marshal_version)
    return (b"PYZ\0" + importlib.util.MAGIC_NUMBER
            + struct.pack("!I", 12 + len(payload)) + payload + toc_bytes)


@pytest.mark.parametrize("compressed", [False, True])
def test_archive_extracts_bytes_without_modifying_source(tmp_path, compressed):
    raw = b"synthetic bytes\0" * 8
    packed = zlib.compress(raw) if compressed else raw
    data = archive_bytes(packed, [(b"sample\0", 0, len(packed), len(raw), int(compressed), b"x")])
    # Real signed executables may have a trailer after the embedded archive.
    path = write_archive(tmp_path, data + b"opaque signature trailer")
    digest = hashlib.sha256(path.read_bytes()).digest()
    version, entries, extract = inspector.read_archive(path)
    assert version == 311
    assert entries["sample"][-1] == "x"
    assert extract("sample") == raw
    assert hashlib.sha256(path.read_bytes()).digest() == digest
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("data, message", [
    (b"plain data", "Not a PyInstaller"),
    (b"prefix" + inspector.COOKIE_MAGIC, "Truncated CArchive cookie"),
    (archive_bytes(package_size=0), "Invalid archive bounds"),
    (archive_bytes(package_size=2**32 - 1), "Invalid archive bounds"),
    (archive_bytes(toc_offset=2**32 - 1), "Invalid archive bounds"),
    (archive_bytes(toc_size=2**32 - 1), "Invalid archive bounds"),
    (archive_bytes(toc_size=1), "Truncated TOC entry header"),
    (archive_bytes(toc_size=inspector.TOC_ENTRY.size), "Invalid TOC entry"),
])
def test_rejects_malformed_archive_cookie_and_toc(tmp_path, data, message):
    with pytest.raises(ValueError, match=message):
        inspector.read_archive(write_archive(tmp_path, data))


@pytest.mark.parametrize("record, message", [
    ((b"name", 0, 4, 4, 0, b"x"), "NUL-terminated"),
    ((b"\0", 0, 4, 4, 0, b"x"), "non-empty"),
    ((b"\xff\0", 0, 4, 4, 0, b"x"), "text encoding"),
    ((b"name\0", 0, 4, 4, 0, b"\xff"), "text encoding"),
    ((b"name\0", 0, 4, 4, 0, b"\0"), "member type"),
    ((b"name\0", 0, 4, 4, 2, b"x"), "compression flag"),
    ((b"name\0", 5, 0, 0, 0, b"x"), "outside payload"),
    ((b"name\0", 1, 4, 4, 0, b"x"), "overlaps TOC"),
    ((b"name\0", 0, 4, 3, 0, b"x"), "size mismatch"),
])
def test_rejects_invalid_archive_members(tmp_path, record, message):
    with pytest.raises(ValueError, match=message):
        inspector.read_archive(write_archive(tmp_path, archive_bytes(records=[record])))


def test_rejects_duplicate_archive_names(tmp_path):
    record = (b"same\0", 0, 4, 4, 0, b"x")
    with pytest.raises(ValueError, match="Duplicate archive entry"):
        inspector.read_archive(write_archive(tmp_path, archive_bytes(records=[record, record])))


def test_archive_size_and_entry_limits(tmp_path, monkeypatch):
    path = write_archive(tmp_path, archive_bytes())
    monkeypatch.setattr(inspector, "MAX_ARCHIVE_BYTES", 10)
    with pytest.raises(ValueError, match="Archive exceeds"):
        inspector.read_archive(path)
    monkeypatch.setattr(inspector, "MAX_ARCHIVE_BYTES", 1000)
    monkeypatch.setattr(inspector, "MAX_TOC_ITEMS", 0)
    with pytest.raises(ValueError, match="Too many archive entries"):
        inspector.read_archive(path)


def test_archive_name_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(inspector, "MAX_NAME_BYTES", 4)
    with pytest.raises(ValueError, match="TOC name exceeds size limit"):
        inspector.read_archive(write_archive(tmp_path, archive_bytes()))


def test_returned_directory_cannot_bypass_validated_archive_bounds(tmp_path):
    _, entries, extract = inspector.read_archive(write_archive(tmp_path, archive_bytes()))
    entries["sample"] = (100_000, 4, 4, 0, "x")
    assert extract("sample") == b"data"


@pytest.mark.parametrize("compressed", [False, True])
def test_archive_raw_size_limit_checked_before_extracting(tmp_path, monkeypatch, compressed):
    raw = b"a" * 32
    packed = zlib.compress(raw) if compressed else raw
    records = [(b"sample\0", 0, len(packed), len(raw), int(compressed), b"x")]
    _, _, extract = inspector.read_archive(write_archive(tmp_path, archive_bytes(packed, records)))
    monkeypatch.setattr(inspector, "MAX_MEMBER_BYTES", 16)
    with pytest.raises(ValueError, match="extraction size limit"):
        extract("sample")


@pytest.mark.parametrize("packed, claimed_size, message", [
    (zlib.compress(b"abc"), 2, "exceeds size limit"),
    (zlib.compress(b"abc"), 4, "Invalid extracted size"),
    (zlib.compress(b"abc")[:-1], 3, "Truncated compressed"),
    (zlib.compress(b"abc") + b"trailer", 3, "trailing stream data"),
    (zlib.compress(b"abc") + zlib.compress(b"def"), 3, "trailing stream data"),
    (b"not zlib", 3, "Invalid compressed"),
])
def test_archive_decompression_requires_one_complete_bounded_stream(tmp_path, packed, claimed_size, message):
    records = [(b"sample\0", 0, len(packed), claimed_size, 1, b"x")]
    _, _, extract = inspector.read_archive(write_archive(tmp_path, archive_bytes(packed, records)))
    with pytest.raises(ValueError, match=message):
        extract("sample")


@pytest.mark.parametrize("marshal_version", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("directory_type", [dict, list, tuple])
def test_pyz_reads_data_only_tocs_across_marshal_formats(marshal_version, directory_type):
    packed = zlib.compress(b"harmless module bytes")
    location = (0, 12, len(packed))
    # Shared location forces marshal v3+ reference handling, without code objects.
    toc = directory_type([("alpha", location), ("beta", location)])
    magic, entries, extract = inspector.read_pyz(pyz_bytes(packed, toc, marshal_version))
    assert magic == importlib.util.MAGIC_NUMBER
    assert entries == {"alpha": location, "beta": location}
    assert extract("alpha") == b"harmless module bytes"


def test_pyz_accepts_utf8_bytes_names_and_namespace_packages():
    toc = [("包.module".encode("utf-8"), (1, 12, len(zlib.compress(b"not application bytecode"))))]
    toc.append(("namespace", (3, 0, 0)))
    _, entries, extract = inspector.read_pyz(pyz_bytes(toc=toc))
    assert "包.module" in entries
    assert extract("包.module") == b"not application bytecode"
    with pytest.raises(ValueError, match="not an executable module"):
        extract("namespace")


@pytest.mark.parametrize("position", ["zero", "start", "middle", "end"])
def test_pyz_namespace_zero_length_accepts_current_payload_position(position):
    packed = zlib.compress(b"harmless module bytes")
    toc_at = 12 + len(packed)
    offset = {"zero": 0, "start": 12, "middle": toc_at - 1, "end": toc_at}[position]
    _, entries, extract = inspector.read_pyz(
        pyz_bytes(packed, [("namespace", (3, offset, 0))]),
    )
    assert entries["namespace"] == (3, offset, 0)
    with pytest.raises(ValueError, match="not an executable module"):
        extract("namespace")


@pytest.mark.parametrize("offset", [-1, 1, 11, "past_end", 2**32])
def test_pyz_namespace_zero_length_still_rejects_invalid_offsets(offset):
    packed = zlib.compress(b"harmless module bytes")
    if offset == "past_end":
        offset = 12 + len(packed) + 1
    with pytest.raises(ValueError, match="namespace offset lies outside payload"):
        inspector.read_pyz(pyz_bytes(packed, [("namespace", (3, offset, 0))]))


@pytest.mark.parametrize("data, message", [
    (b"PYZ\0", "PYZ header"),
    (b"BAD!" + pyz_bytes()[4:], "PYZ header"),
    (pyz_bytes()[:6] + b"XX" + pyz_bytes()[8:], "PYZ header"),
    (pyz_bytes()[:8] + struct.pack("!I", 11) + pyz_bytes()[12:], "TOC offset"),
    (pyz_bytes()[:8] + struct.pack("!I", 2**32 - 1) + pyz_bytes()[12:], "TOC offset"),
    (pyz_bytes() + b"trailer", "Trailing data"),
    (pyz_bytes(toc=1), "TOC structure"),
])
def test_rejects_invalid_pyz_header_and_directory(data, message):
    with pytest.raises(ValueError, match=message):
        inspector.read_pyz(data)


@pytest.mark.parametrize("toc, message", [
    ([1], "directory entry"),
    ([("only_name",)], "directory entry"),
    ([(1, (0, 12, 1))], "module name"),
    ([("", (0, 12, 1))], "module name"),
    ([("bad\0name", (0, 12, 1))], "module name"),
    ([("same", (0, 12, 1)), ("same", (0, 12, 1))], "duplicate PYZ module name"),
    ([("name", 1)], "module location"),
    ([("name", (0, 12))], "module location"),
    ([("name", (0, "12", 1))], "module location"),
    ([("name", (4, 12, 1))], "module type"),
    ([("name", (0, 12, -1))], "module type or size"),
    ([("name", (0, 11, 1))], "outside payload"),
    ([("name", (0, 12, 0))], "outside payload"),
    ([("name", (0, 2**32, 1))], "outside payload"),
    ([("name", (0, 12, 2**32))], "overlaps TOC"),
])
def test_rejects_invalid_pyz_module_records(toc, message):
    with pytest.raises(ValueError, match=message):
        inspector.read_pyz(pyz_bytes(toc=toc))


@pytest.mark.parametrize("toc_bytes, message", [
    (b"c", "Unsupported marshal type"),  # Reject before any code deserialization.
    (b"[" + struct.pack("<i", 2**31 - 1), "container length"),
    (b"[" + struct.pack("<i", -1), "container length"),
    (b"u" + struct.pack("<i", 2**31 - 1), "string length"),
    (b"u" + struct.pack("<i", -1), "string length"),
    (b"u" + struct.pack("<i", 4) + b"a", "Truncated PYZ TOC"),
    (b"r" + struct.pack("<i", 0), "reference"),
    (b"\xdb" + struct.pack("<i", 1) + b"r" + struct.pack("<i", 0), "cyclic"),
    (b")\x01" * 18 + b"i" + struct.pack("<i", 0), "complexity limit"),
    (b"l" + struct.pack("<i", 6), "integer is too large"),
    (b"l" + struct.pack("<i", 1) + struct.pack("<H", 32768), "integer digit"),
    (b"{u\x01\0\0\0ai\0\0\0\0u\x01\0\0\0ai\0\0\0\x000", "duplicate PYZ TOC key"),
])
def test_untrusted_pyz_marshal_is_bounded_and_data_only(toc_bytes, message):
    with pytest.raises(ValueError, match=message):
        inspector.read_pyz(pyz_bytes(toc_bytes=toc_bytes))


def test_pyz_toc_size_limit(monkeypatch):
    monkeypatch.setattr(inspector, "MAX_PYZ_TOC_BYTES", 4)
    with pytest.raises(ValueError, match="TOC exceeds size limit"):
        inspector.read_pyz(pyz_bytes())


def test_pyz_decompression_limit_and_data_entry_rejection(monkeypatch):
    packed = zlib.compress(b"a" * 64)
    _, _, extract = inspector.read_pyz(pyz_bytes(packed))
    monkeypatch.setattr(inspector, "MAX_MODULE_BYTES", 16)
    with pytest.raises(ValueError, match="exceeds size limit"):
        extract("synthetic.module")
    _, _, extract = inspector.read_pyz(pyz_bytes(packed, [("data", (2, 12, len(packed)))]))
    with pytest.raises(ValueError, match="not an executable module"):
        extract("data")


def test_returned_directory_cannot_bypass_validated_pyz_bounds():
    _, entries, extract = inspector.read_pyz(pyz_bytes())
    entries["synthetic.module"] = (0, 100_000, 1)
    assert extract("synthetic.module") == b"not application bytecode"


def test_cli_listing_is_read_only_and_never_imports_xdis_or_application_code(tmp_path, monkeypatch, capsys):
    pyz = pyz_bytes(toc=[("synthetic.module", (0, 12, len(zlib.compress(b"not application bytecode"))))])
    path = write_archive(tmp_path, archive_bytes(pyz, [(b"PYZ.pyz\0", 0, len(pyz), len(pyz), 0, b"z")]))
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith(("xdis", "synthetic")):
            raise AssertionError("Listing must never import bytecode tooling or application code")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    before = path.read_bytes()
    inspector.main([str(path), "--filter", "MODULE"])
    assert "synthetic.module" in capsys.readouterr().out
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("extra_args, message", [
    (["--function", "example"], "requires --module"),
    (["--module", "absent"], "Module not found"),
])
def test_cli_rejects_inconsistent_requests(tmp_path, capsys, extra_args, message):
    pyz = pyz_bytes()
    path = write_archive(tmp_path, archive_bytes(pyz, [(b"PYZ.pyz\0", 0, len(pyz), len(pyz), 0, b"z")]))
    with pytest.raises(SystemExit) as exc:
        inspector.main([str(path), *extra_args])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("pyz_count", [0, 2])
def test_cli_rejects_missing_or_ambiguous_pyz(tmp_path, capsys, pyz_count):
    records = [(f"PYZ{index}.pyz\0".encode(), 0, 4, 4, 0, b"z") for index in range(pyz_count)]
    path = write_archive(tmp_path, archive_bytes(records=records))
    with pytest.raises(SystemExit) as exc:
        inspector.main([str(path)])
    assert exc.value.code == 2
    assert "exactly one embedded PYZ" in capsys.readouterr().err
