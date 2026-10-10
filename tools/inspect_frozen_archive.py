"""Read-only PyInstaller archive inspection. Never imports or runs application code.

Usage: python tools/inspect_frozen_archive.py app.exe [--module package.name]
Listing uses only the stdlib. Static cross-version bytecode inspection requires
xdis in the analyst environment (not a runtime dependency of this project).
"""
import argparse
import io
from pathlib import Path
import struct
import zlib


COOKIE_MAGIC = b"MEI\014\013\012\013\016"
COOKIE = struct.Struct("!8sIIII64s")
TOC_ENTRY = struct.Struct("!iIIIBc")
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_PYZ_TOC_BYTES = 32 * 1024 * 1024
MAX_MODULE_BYTES = 32 * 1024 * 1024
MAX_TOC_ITEMS = 100_000
MAX_NAME_BYTES = 16 * 1024


def _decompress(content, limit, expected_size=None):
    """One complete zlib stream, with a hard limit before output allocation."""
    try:
        inflater = zlib.decompressobj()
        result = inflater.decompress(content, limit + 1)
    except zlib.error as exc:
        raise ValueError("Invalid compressed archive member") from exc
    if len(result) > limit or inflater.unconsumed_tail:
        raise ValueError("Decompressed member exceeds size limit")
    if not inflater.eof or inflater.unused_data:
        raise ValueError("Truncated compressed member or trailing stream data")
    if expected_size is not None and len(result) != expected_size:
        raise ValueError("Invalid extracted size")
    return result


def read_archive(path):
    """Inspect a modern (88-byte cookie) CArchive; extract returns bytes only."""
    with Path(path).open("rb") as stream:
        if stream.seek(0, 2) > MAX_ARCHIVE_BYTES:
            raise ValueError("Archive exceeds inspection size limit")
        stream.seek(0)
        data = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds inspection size limit")
    cookie_at = data.rfind(COOKIE_MAGIC)
    if cookie_at < 0:
        raise ValueError("Not a PyInstaller CArchive")
    if cookie_at + COOKIE.size > len(data):
        raise ValueError("Truncated CArchive cookie")
    _, package_size, toc_offset, toc_size, version, _ = COOKIE.unpack_from(data, cookie_at)
    start = cookie_at + COOKIE.size - package_size
    if (package_size < COOKIE.size or start < 0
            or toc_offset > package_size - COOKIE.size
            or toc_size > package_size - COOKIE.size - toc_offset):
        raise ValueError("Invalid archive bounds")
    entries, at, toc_end = {}, start + toc_offset, start + toc_offset + toc_size
    while at < toc_end:
        if toc_end - at < TOC_ENTRY.size:
            raise ValueError("Truncated TOC entry header")
        size, position, packed_size, raw_size, compressed, kind = TOC_ENTRY.unpack_from(data, at)
        if size <= TOC_ENTRY.size or size > toc_end - at:
            raise ValueError("Invalid TOC entry")
        name_field = data[at + TOC_ENTRY.size:at + size]
        if b"\0" not in name_field or not name_field.split(b"\0", 1)[0]:
            raise ValueError("TOC name must be non-empty and NUL-terminated")
        if len(name_field) > MAX_NAME_BYTES:
            raise ValueError("TOC name exceeds size limit")
        try:
            name = name_field.split(b"\0", 1)[0].decode("utf-8")
            kind = kind.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid TOC text encoding") from exc
        if not kind.isalpha():
            raise ValueError("Invalid archive member type")
        if name in entries:
            raise ValueError(f"Duplicate archive entry: {name}")
        if compressed not in (0, 1):
            raise ValueError("Invalid compression flag")
        if position > toc_offset or packed_size > toc_offset - position:
            raise ValueError("Archive member overlaps TOC or lies outside payload")
        if not compressed and packed_size != raw_size:
            raise ValueError("Uncompressed member size mismatch")
        if len(entries) >= MAX_TOC_ITEMS:
            raise ValueError("Too many archive entries")
        entries[name] = (position, packed_size, raw_size, compressed, kind)
        at += size

    validated_entries = entries.copy()

    def extract(name):
        position, packed_size, raw_size, compressed, _ = validated_entries[name]
        if raw_size > MAX_MEMBER_BYTES:
            raise ValueError("Archive member exceeds extraction size limit")
        content = data[start + position:start + position + packed_size]
        if compressed:
            content = _decompress(content, raw_size, expected_size=raw_size)
        if len(content) != raw_size:
            raise ValueError("Invalid extracted size")
        return content

    return version, entries, extract


class _TocMarshalReader:
    """Data-only marshal subset for PYZ TOCs; no code objects are deserialized.

    marshal.loads can allocate containers from attacker-provided lengths before
    validating the remaining bytes. Bound lengths, nesting and references here;
    PYZ directories need only names and integer position tuples.
    """

    _NULL = object()
    _PENDING = object()

    def __init__(self, data):
        if len(data) > MAX_PYZ_TOC_BYTES:
            raise ValueError("PYZ TOC exceeds size limit")
        self.data, self.at, self.refs, self.items = data, 0, [], 0

    def take(self, size):
        if size < 0 or size > len(self.data) - self.at:
            raise ValueError("Truncated PYZ TOC")
        value = self.data[self.at:self.at + size]
        self.at += size
        return value

    def integer(self):
        return struct.unpack("<i", self.take(4))[0]

    def object(self, depth=0, allow_null=False):
        self.items += 1
        if depth > 16 or self.items > MAX_TOC_ITEMS * 8:
            raise ValueError("PYZ TOC complexity limit exceeded")
        tag = self.take(1)[0]
        is_ref, tag = bool(tag & 0x80), chr(tag & 0x7f)
        ref_index = None
        if is_ref:
            ref_index = len(self.refs)
            self.refs.append(self._PENDING)
        if tag == "0" and allow_null and not is_ref:
            return self._NULL
        if tag == "r":
            index = self.integer()
            if not 0 <= index < len(self.refs) or self.refs[index] is self._PENDING:
                raise ValueError("Invalid or cyclic PYZ TOC reference")
            value = self.refs[index]
        elif tag == "i":
            value = self.integer()
        elif tag == "I":
            value = struct.unpack("<q", self.take(8))[0]
        elif tag == "l":
            digits = self.integer()
            if abs(digits) > 5:
                raise ValueError("PYZ TOC integer is too large")
            value = 0
            for index in range(abs(digits)):
                digit = struct.unpack("<H", self.take(2))[0]
                if digit >= 32768:
                    raise ValueError("Invalid PYZ TOC integer digit")
                value |= digit << (15 * index)
            if digits < 0:
                value = -value
        elif tag in ("u", "a", "A", "s", "t", "z", "Z"):
            size = self.take(1)[0] if tag in ("z", "Z") else self.integer()
            if not 0 <= size <= 1024 * 1024:
                raise ValueError("Invalid PYZ TOC string length")
            raw = self.take(size)
            value = raw if tag in ("s", "t") else raw.decode("utf-8" if tag == "u" else "ascii")
        elif tag in ("(", ")", "["):
            count = self.take(1)[0] if tag == ")" else self.integer()
            if not 0 <= count <= MAX_TOC_ITEMS:
                raise ValueError("Invalid PYZ TOC container length")
            values = [self.object(depth + 1) for _ in range(count)]
            value = values if tag == "[" else tuple(values)
        elif tag == "{":
            value = {}
            while True:
                key = self.object(depth + 1, allow_null=True)
                if key is self._NULL:
                    break
                if len(value) >= MAX_TOC_ITEMS or not isinstance(key, (str, bytes)) or key in value:
                    raise ValueError("Invalid or duplicate PYZ TOC key")
                value[key] = self.object(depth + 1)
        else:
            # In particular, 'c' (application code) is never accepted in the TOC.
            raise ValueError(f"Unsupported marshal type in PYZ TOC: {tag!r}")
        if ref_index is not None:
            self.refs[ref_index] = value
        return value


def read_pyz(pyz):
    """Return bytecode magic, validated module directory and a bytes-only reader."""
    if len(pyz) < 12 or pyz[:4] != b"PYZ\0" or pyz[6:8] != b"\r\n":
        raise ValueError("Invalid or truncated PYZ header")
    toc_at = struct.unpack_from("!I", pyz, 8)[0]
    if not 12 <= toc_at < len(pyz):
        raise ValueError("Invalid PYZ TOC offset")
    reader = _TocMarshalReader(pyz[toc_at:])
    toc = reader.object()
    if reader.at != len(reader.data):
        raise ValueError("Trailing data after PYZ TOC")
    if not isinstance(toc, (dict, list, tuple)):
        raise ValueError("Invalid PYZ TOC structure")
    pairs = toc.items() if isinstance(toc, dict) else toc
    entries = {}
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Invalid PYZ directory entry")
        name, location = pair
        if isinstance(name, bytes):
            name = name.decode("utf-8")
        if not isinstance(name, str) or not name or "\0" in name or name in entries:
            raise ValueError("Invalid or duplicate PYZ module name")
        if (not isinstance(location, (list, tuple)) or len(location) != 3
                or any(type(part) is not int for part in location)):
            raise ValueError("Invalid PYZ module location")
        kind, offset, length = location
        if kind not in (0, 1, 2, 3) or length < 0:
            raise ValueError("Invalid PYZ module type or size")
        if kind == 3 and length == 0:
            # Writers use either zero or their current payload position for a
            # namespace package, which intentionally stores no module bytes.
            if offset != 0 and not 12 <= offset <= toc_at:
                raise ValueError("PYZ namespace offset lies outside payload")
        elif offset < 12 or length == 0 or offset > toc_at or length > toc_at - offset:
            raise ValueError("PYZ module overlaps TOC or lies outside payload")
        entries[name] = (kind, offset, length)

    validated_entries = entries.copy()

    def extract_module(name):
        kind, offset, length = validated_entries[name]
        if kind not in (0, 1):
            raise ValueError("PYZ entry is not an executable module or package")
        return _decompress(pyz[offset:offset + length], MAX_MODULE_BYTES)

    return pyz[4:8], entries, extract_module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--module")
    parser.add_argument("--function", help="Statically disassemble one named function")
    parser.add_argument("--filter", default="")
    args = parser.parse_args(argv)
    if args.function and not args.module:
        parser.error("--function requires --module")
    try:
        version, entries, extract = read_archive(args.archive)
        print(f"Python version: {version}; archive entries: {len(entries)}")
        pyz_names = [name for name, entry in entries.items() if entry[-1] == "z"]
        if len(pyz_names) != 1:
            raise ValueError("Expected exactly one embedded PYZ archive")
        magic, toc, extract_module = read_pyz(extract(pyz_names[0]))
        if args.module is not None and args.module not in toc:
            raise ValueError(f"Module not found in PYZ: {args.module}")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not args.module:
        for name in sorted(toc):
            if args.filter.lower() in name.lower():
                print(name)
        return
    try:
        code_bytes = extract_module(args.module)
        from xdis.magics import magic2int
        from xdis.unmarshal import load_code
        code = load_code(io.BytesIO(code_bytes), magic2int(magic))
    except (ImportError, ValueError, EOFError, KeyError, TypeError) as exc:
        parser.error(f"Static bytecode inspection failed: {exc}")

    def describe(obj, prefix=""):
        if not hasattr(obj, "co_code"):
            return
        if args.function is None:
            print(prefix + obj.co_name, "names=", obj.co_names)
        elif obj.co_name == args.function:
            from xdis.bytecode import Bytecode
            from xdis.op_imports import get_opcode_module
            from xdis.version_info import PythonImplementation
            opcode = get_opcode_module((version // 100, version % 100), PythonImplementation.CPython)
            print("Function:", obj.co_name, "locals:", obj.co_varnames)
            print(Bytecode(obj, opcode).dis())
        for constant in obj.co_consts:
            if hasattr(constant, "co_code"):
                describe(constant, prefix + "  ")
    describe(code)


if __name__ == "__main__":
    main()
