# -*- coding: utf-8 -*-
"""
Compile Qt .ts translation files into the binary .qm format.

This is a stand-in for Qt's `lrelease`, which is not shipped with the QGIS
Windows installation. It writes the subset of the .qm format QTranslator
needs: a hash table and a message block.

    python tools/build_qm.py                 # compile every .ts found
    python tools/build_qm.py path/to/file.ts # compile one file

The .ts file stays the source of truth and can be edited by hand or in
Qt Linguist; run this afterwards to regenerate the .qm.
"""

import os
import struct
import sys
import xml.etree.ElementTree as ElementTree

MAGIC = bytes([0x3c, 0xb8, 0x64, 0x18, 0xca, 0xef, 0x9c, 0x95,
               0xcd, 0x21, 0x1c, 0xbf, 0x60, 0xa1, 0xbd, 0xdd])

# Section tags, as read by QTranslatorPrivate::do_load
SECTION_HASHES = 0x42
SECTION_MESSAGES = 0x69
SECTION_LANGUAGE = 0xa7

# Sub-tags inside a message, as read by QTranslatorPrivate::do_translate
TAG_END = 1
TAG_TRANSLATION = 3
TAG_SOURCETEXT = 6
TAG_CONTEXT = 7


def elf_hash(data):
    """The hash QTranslator looks messages up by (elfHash in qtranslator.cpp)."""
    value = 0
    for byte in data:
        value = ((value << 4) + byte) & 0xFFFFFFFF
        high = value & 0xF0000000
        if high:
            value ^= high >> 24
        value &= (~high) & 0xFFFFFFFF
    return value or 1


def _bytes_field(data):
    """QDataStream serialization of a QByteArray: length, then raw bytes."""
    return struct.pack(">I", len(data)) + data


def _string_field(text):
    """QDataStream serialization of a QString: byte length, then UTF-16BE."""
    encoded = text.encode("utf-16-be")
    return struct.pack(">I", len(encoded)) + encoded


def _block(tag, payload):
    return struct.pack(">BI", tag, len(payload)) + payload


def compile_ts(ts_path, qm_path=None):
    """Read a .ts file and write the matching .qm. Returns the message count."""
    qm_path = qm_path or os.path.splitext(ts_path)[0] + ".qm"
    root = ElementTree.parse(ts_path).getroot()

    messages, offsets = bytearray(), []
    skipped = 0

    for context in root.findall("context"):
        name = context.findtext("name", "").encode("utf-8")
        for message in context.findall("message"):
            translation = message.find("translation")
            source = message.findtext("source", "")
            # untranslated or obsolete entries simply fall back to the source
            if translation is None or not (translation.text or ""):
                skipped += 1
                continue
            if translation.get("type") in ("unfinished", "obsolete", "vanished"):
                skipped += 1
                continue

            source_bytes = source.encode("utf-8")
            # The lookup hash covers source text plus disambiguation comment;
            # we do not use comments, so it is the source text alone.
            offsets.append((elf_hash(source_bytes), len(messages)))

            messages += struct.pack(">B", TAG_TRANSLATION)
            messages += _string_field(translation.text)
            messages += struct.pack(">B", TAG_SOURCETEXT)
            messages += _bytes_field(source_bytes)
            messages += struct.pack(">B", TAG_CONTEXT)
            messages += _bytes_field(name)
            messages += struct.pack(">B", TAG_END)

    # The offset table has to be sorted; QTranslator binary searches it.
    hashes = bytearray()
    for value, offset in sorted(offsets):
        hashes += struct.pack(">II", value, offset)

    language = root.get("language", "")
    payload = MAGIC
    payload += _block(SECTION_HASHES, bytes(hashes))
    payload += _block(SECTION_MESSAGES, bytes(messages))
    if language:
        payload += _block(SECTION_LANGUAGE, language.encode("utf-8"))

    with open(qm_path, "wb") as handle:
        handle.write(payload)

    print("%s -> %s (%d messages, %d skipped, %d bytes)" % (
        os.path.basename(ts_path), os.path.basename(qm_path),
        len(offsets), skipped, len(payload)))
    return len(offsets)


def main(argv):
    if len(argv) > 1:
        targets = argv[1:]
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        targets = []
        for folder, _, files in os.walk(root):
            targets += [os.path.join(folder, f)
                        for f in sorted(files) if f.endswith(".ts")]
    if not targets:
        print("no .ts files found")
        return 1
    for target in targets:
        compile_ts(target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
