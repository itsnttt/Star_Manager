#!/usr/bin/env python3
"""Repack a UnityFS bundle with LZ4 block compression.

The payload bytes and UnityFS directory entries are kept byte-for-byte.  Only
the outer block layout and compression flags are rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import UnityPy
from UnityPy.helpers import CompressionHelper


U32 = struct.Struct(">I")
U16 = struct.Struct(">H")
U64 = struct.Struct(">Q")
HEADER_FLAGS_LZ4 = 0xC2  # blocks-info-at-end + combined directory + LZ4
BLOCK_FLAG_LZ4 = 0x02


def _c_string(data: bytes, pos: int) -> tuple[bytes, int]:
    end = data.find(b"\0", pos)
    if end < 0:
        raise ValueError("unterminated UnityFS header string")
    return data[pos:end], end + 1


def _read_u32(data: bytes, pos: int) -> tuple[int, int]:
    return U32.unpack_from(data, pos)[0], pos + 4


def _read_u64(data: bytes, pos: int) -> tuple[int, int]:
    return U64.unpack_from(data, pos)[0], pos + 8


def _parse_bundle(
    data: bytes,
) -> tuple[bytes, list[tuple[int, int, int]], list[tuple[int, int, int, bytes]], bytes]:
    pos = 0
    signature, pos = _c_string(data, pos)
    if signature != b"UnityFS":
        raise ValueError(f"only UnityFS is supported, got {signature!r}")
    version, pos = _read_u32(data, pos)
    if version != 6:
        raise ValueError(f"only UnityFS format 6 is supported, got {version}")
    _player, pos = _c_string(data, pos)
    _engine, pos = _c_string(data, pos)
    declared_size, pos = _read_u64(data, pos)
    compressed_info_size, pos = _read_u32(data, pos)
    uncompressed_info_size, pos = _read_u32(data, pos)
    flags, pos = _read_u32(data, pos)
    if declared_size > len(data):
        raise ValueError(f"declared bundle size {declared_size} exceeds file size {len(data)}")
    if flags & 0x1400:
        raise ValueError("encrypted UnityFS bundles are not supported")

    info_at_end = bool(flags & 0x80)
    if info_at_end:
        info_pos = declared_size - compressed_info_size
        data_pos = pos
    else:
        info_pos = pos
        data_pos = pos + compressed_info_size
    if flags & 0x200:
        data_pos = (data_pos + 15) & ~15

    info_compressed = data[info_pos : info_pos + compressed_info_size]
    info_mode = flags & 0x3F
    if info_mode == 0:
        info = info_compressed
    elif info_mode in (2, 3):
        info = CompressionHelper.decompress_lz4(info_compressed, uncompressed_info_size)
    elif info_mode == 1:
        info = CompressionHelper.decompress_lzma(info_compressed)
    else:
        raise ValueError(f"unsupported UnityFS block-info compression flag {info_mode}")
    if len(info) != uncompressed_info_size:
        raise ValueError("UnityFS block-info size mismatch")

    ipos = 16  # uncompressed data hash; preserve it in the rebuilt directory
    block_count, ipos = _read_u32(info, ipos)
    blocks: list[tuple[int, int, int]] = []
    for _ in range(block_count):
        uncompressed, ipos = _read_u32(info, ipos)
        compressed, ipos = _read_u32(info, ipos)
        flags16 = U16.unpack_from(info, ipos)[0]
        ipos += 2
        blocks.append((uncompressed, compressed, flags16))

    node_count, ipos = _read_u32(info, ipos)
    nodes: list[tuple[int, int, int, bytes]] = []
    for _ in range(node_count):
        offset, ipos = _read_u64(info, ipos)
        size, ipos = _read_u64(info, ipos)
        node_flags, ipos = _read_u32(info, ipos)
        name, ipos = _c_string(info, ipos)
        nodes.append((offset, size, node_flags, name))

    # Decompress the original data blocks and concatenate the logical payload.
    chunks: list[bytes] = []
    cursor = data_pos
    for uncompressed, compressed, block_flags in blocks:
        raw = data[cursor : cursor + compressed]
        cursor += compressed
        mode = block_flags & 0x3F
        if mode == 0:
            decoded = raw
        elif mode in (2, 3):
            decoded = CompressionHelper.decompress_lz4(raw, uncompressed)
        elif mode == 1:
            decoded = CompressionHelper.decompress_lzma(raw)
        else:
            raise ValueError(f"unsupported UnityFS data block compression flag {mode}")
        if len(decoded) != uncompressed:
            raise ValueError("UnityFS data block size mismatch")
        chunks.append(decoded)
    payload = b"".join(chunks)
    for offset, size, _node_flags, _name in nodes:
        if offset + size > len(payload):
            raise ValueError("UnityFS directory entry exceeds decompressed payload")

    return info[:16], blocks, nodes, payload


def repack_lz4(source: Path, target: Path) -> dict:
    original = source.read_bytes()
    data_hash, _old_blocks, nodes, payload = _parse_bundle(original)
    compressed_payload, new_blocks = CompressionHelper.chunk_based_compress(payload, BLOCK_FLAG_LZ4)

    info_writer = bytearray(data_hash)
    info_writer += U32.pack(len(new_blocks))
    for uncompressed, compressed, flags in new_blocks:
        info_writer += U32.pack(uncompressed)
        info_writer += U32.pack(compressed)
        info_writer += U16.pack(flags)
    info_writer += U32.pack(len(nodes))
    for offset, size, flags, name in nodes:
        info_writer += U64.pack(offset)
        info_writer += U64.pack(size)
        info_writer += U32.pack(flags)
        info_writer += name + b"\0"
    info_raw = bytes(info_writer)
    info_compressed = CompressionHelper.compress_lz4(info_raw)

    # header_tail starts immediately after the two UnityFS version strings and
    # contains the old numeric fields. Keep the prefix strings untouched.
    # Recompute the prefix by walking the strings, avoiding assumptions about
    # player/engine string lengths.
    p = 0
    _sig, p = _c_string(original, p)
    p += 4
    _player, p = _c_string(original, p)
    _engine, p = _c_string(original, p)
    prefix = original[:p]
    result_size = len(prefix) + 20 + len(compressed_payload) + len(info_compressed)
    output = prefix + struct.pack(">QIII", result_size, len(info_compressed), len(info_raw), HEADER_FLAGS_LZ4)
    output += compressed_payload + info_compressed
    if len(output) != result_size:
        raise AssertionError("rebuilt UnityFS size mismatch")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(output)
    # UnityPy validates the rebuilt container and gives a useful semantic
    # object count for the report.
    env = UnityPy.load(output)
    result = {
        "source": str(source),
        "target": str(target),
        "source_bytes": len(original),
        "target_bytes": len(output),
        "source_sha256": hashlib.sha256(original).hexdigest(),
        "target_sha256": hashlib.sha256(output).hexdigest(),
        "payload_bytes": len(payload),
        "block_count": len(new_blocks),
        "objects": len(env.objects),
        "assets": len(env.assets),
        "container_entries": len(env.container),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = repack_lz4(args.source, args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
