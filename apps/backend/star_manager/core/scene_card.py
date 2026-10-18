from __future__ import annotations

from pathlib import Path
from typing import Any

from star_manager.core.card_parser import (
    UNIVERSAL_AUTO_RESOLVER_ID,
    extract_auto_resolver_records_from_card_data,
    extract_character_profile_from_card_data,
    json_safe_msgpack,
    try_unpack_msgpack,
)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
STUDIO_SCENE_MARKER = "【StudioNEOV2】"
KKEX_MARKER = b"KKEx"
SCENE_MAP_GUID_FIELD = "mapInfoGUID"
SCENE_ITEM_INFO_FIELD = "itemInfo"
SCENE_PATTERN_INFO_FIELD = "patternInfo"
AIS_CHARACTER_MARKER = "【AIS_Chara】"


def find_png_end(data: bytes) -> int:
    """Return the byte offset immediately after the visible PNG image."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("Input is not a PNG file.")

    cursor = len(PNG_SIGNATURE)
    while cursor + 12 <= len(data):
        chunk_size = int.from_bytes(data[cursor : cursor + 4], "big")
        chunk_type = data[cursor + 4 : cursor + 8]
        chunk_end = cursor + 12 + chunk_size
        if chunk_end > len(data):
            raise ValueError("PNG chunk data is truncated.")
        cursor = chunk_end
        if chunk_type == b"IEND":
            return cursor
    raise ValueError("PNG IEND chunk was not found.")


def extract_scene_data(file_bytes: bytes) -> bytes:
    """Return IEND CRC plus the Studio scene payload, if present."""
    png_end = find_png_end(file_bytes)
    if png_end + 1 >= len(file_bytes):
        return b""
    return file_bytes[png_end - 4 :]


def _read_dotnet_string(data: bytes, offset: int) -> tuple[str, int] | None:
    """Read BinaryWriter.Write(string) data without decoding the whole scene."""
    value = 0
    shift = 0
    cursor = offset
    while cursor < len(data) and shift <= 35:
        byte = data[cursor]
        cursor += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            end = cursor + value
            if value <= 0 or end > len(data):
                return None
            try:
                return data[cursor:end].decode("utf-8"), end
            except UnicodeDecodeError:
                return None
        shift += 7
    return None


def _embedded_card_start(data: bytes, marker_offset: int) -> int | None:
    """Find the product number immediately preceding an embedded AIS marker."""
    marker = AIS_CHARACTER_MARKER
    for prefix_offset in range(max(0, marker_offset - 5), marker_offset):
        decoded = _read_dotnet_string(data, prefix_offset)
        if not decoded or decoded[0] != marker or decoded[1] != marker_offset + len(marker.encode("utf-8")):
            continue
        product_offset = prefix_offset - 4
        if product_offset < 0:
            continue
        product_no = int.from_bytes(data[product_offset:prefix_offset], "little", signed=True)
        if 0 <= product_no <= 1000:
            return product_offset
    return None


def _read_embedded_character_card(data: bytes, marker_offset: int) -> tuple[int, int] | None:
    """Return ``(card_start, card_end)`` for one embedded AIS character."""
    card_start = _embedded_card_start(data, marker_offset)
    if card_start is None:
        return None
    marker_read = None
    for prefix_offset in range(max(0, marker_offset - 5), marker_offset):
        candidate = _read_dotnet_string(data, prefix_offset)
        if candidate and candidate[0] == AIS_CHARACTER_MARKER and candidate[1] == marker_offset + len(AIS_CHARACTER_MARKER.encode("utf-8")):
            marker_read = candidate
            break
    if not marker_read:
        return None
    _, cursor = marker_read
    try:
        version = _read_dotnet_string(data, cursor)
        if not version:
            return None
        _, cursor = version
        if cursor + 4 > len(data):
            return None
        cursor += 4  # language
        user_id = _read_dotnet_string(data, cursor)
        if not user_id:
            return None
        _, cursor = user_id
        data_id = _read_dotnet_string(data, cursor)
        if not data_id:
            return None
        _, cursor = data_id
        if cursor + 4 > len(data):
            return None
        header_size = int.from_bytes(data[cursor : cursor + 4], "little", signed=True)
        cursor += 4
        if header_size <= 0 or header_size > 16 * 1024 * 1024 or cursor + header_size + 8 > len(data):
            return None
        cursor += header_size
        block_size = int.from_bytes(data[cursor : cursor + 8], "little", signed=True)
        cursor += 8
        if block_size <= 0 or block_size > len(data) - cursor:
            return None
        return card_start, cursor + block_size
    except (OverflowError, ValueError):
        return None


def extract_scene_character_cards(file_bytes: bytes) -> list[dict[str, Any]]:
    """Extract embedded AIS character-card payloads from a StudioNEOV2 PNG.

    Studio stores an AIS card after each ``OCIChar`` object. The card begins
    with a little-endian product number and a .NET string marker, then uses the
    same version/id/block layout as a regular character-card PNG. The visible
    scene PNG is reused as the cover when the extracted payload is exported.
    """
    try:
        png_end = find_png_end(file_bytes)
    except (TypeError, ValueError):
        return []

    marker_bytes = AIS_CHARACTER_MARKER.encode("utf-8")
    cards: list[dict[str, Any]] = []
    cursor = png_end
    seen_ranges: set[tuple[int, int]] = set()
    while cursor < len(file_bytes):
        marker_offset = file_bytes.find(marker_bytes, cursor)
        if marker_offset < 0:
            break
        parsed = _read_embedded_character_card(file_bytes, marker_offset)
        if parsed:
            card_start, card_end = parsed
            key = (card_start, card_end)
            if key not in seen_ranges:
                seen_ranges.add(key)
                payload = file_bytes[card_start:card_end]
                card_data = b"\x00\x00\x00\x00" + payload
                try:
                    profile = extract_character_profile_from_card_data(card_data)
                except (TypeError, ValueError, IndexError, OverflowError):
                    profile = {}
                cards.append(
                    {
                        "index": len(cards),
                        "payload": payload,
                        "name": str(profile.get("fullname") or "").strip(),
                        "sex": profile.get("sex"),
                        "profile": profile,
                    }
                )
            cursor = card_end
        else:
            cursor = marker_offset + len(marker_bytes)
    return cards


def _iter_kkex_payloads(scene_data: bytes) -> list[dict[str, Any]]:
    """Decode length-prefixed Studio KKEx blocks.

    Studio scene files use a small binary envelope for plugin blocks:
    ``KKEx`` + little-endian version + little-endian payload size + MessagePack.
    The embedded character cards have a MessagePack block-table entry named
    ``KKEx`` too, so only envelopes whose version and payload size line up with
    a complete MessagePack value are accepted here.
    """
    payloads: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(scene_data):
        marker_offset = scene_data.find(KKEX_MARKER, cursor)
        if marker_offset < 0:
            break
        header_end = marker_offset + len(KKEX_MARKER) + 8
        if header_end > len(scene_data):
            break

        version_offset = marker_offset + len(KKEX_MARKER)
        version = int.from_bytes(scene_data[version_offset : version_offset + 4], "little")
        size_offset = version_offset + 4
        payload_size = int.from_bytes(scene_data[size_offset : size_offset + 4], "little")
        payload_start = size_offset + 4
        payload_end = payload_start + payload_size
        if version < 0 or payload_size <= 0 or payload_end > len(scene_data):
            cursor = marker_offset + len(KKEX_MARKER)
            continue

        decoded = try_unpack_msgpack(scene_data, payload_start)
        if not decoded or decoded[1] != payload_end or not isinstance(decoded[0], dict):
            cursor = marker_offset + len(KKEX_MARKER)
            continue

        payloads.append(
            {
                "offset": marker_offset,
                "version": version,
                "size": payload_size,
                "payload": decoded[0],
            }
        )
        cursor = payload_end
    return payloads


def _scene_dependency_record(guid: object, *, index: int) -> dict[str, Any] | None:
    mod_id = str(guid or "").strip()
    if not mod_id:
        return None
    return {
        "id": f"scene:{mod_id}:{index}",
        "ModID": mod_id,
        "Name": mod_id,
        "Property": "StudioScene.Map",
        "DependencyType": "scene",
        "mapInfoGUID": mod_id,
    }


def _decode_scene_uar_record(value: object) -> dict[str, Any] | None:
    """Decode one scene UAR record, including its nested MessagePack form."""
    if isinstance(value, bytes):
        decoded = try_unpack_msgpack(value)
        value = decoded[0] if decoded else None
    if not isinstance(value, dict):
        return None
    return value


def _scene_item_dependency_record(
    record: dict[str, Any],
    *,
    dependency_type: str,
    property_name: str,
    index: int,
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Normalize scene item/pattern records into the card dependency shape."""
    mod_id = str(record.get("ModID") or record.get("GUID") or "").strip()
    if not mod_id:
        return None
    context = context or {}
    dependency: dict[str, Any] = {
        "id": f"{dependency_type}:{mod_id}:{record.get('Slot', '')}:{record.get('LocalSlot', '')}:{index}",
        "ModID": mod_id,
        "Name": mod_id,
        "Property": property_name,
        "DependencyType": dependency_type,
    }
    for field in ("CategoryNo", "Slot", "LocalSlot", "SceneDicKey", "SceneObjectOrder"):
        value = record.get(field)
        if value is None and field == "CategoryNo":
            value = record.get("Category")
        if value is None:
            value = context.get(field)
        if value is not None:
            dependency[field] = value
    return dependency


def _scene_uar_dependencies(metadata: dict[str, Any], start_index: int) -> list[dict[str, Any]]:
    """Extract scene item and pattern dependencies from the current UAR format."""
    dependencies: list[dict[str, Any]] = []
    index = start_index

    item_info = metadata.get(SCENE_ITEM_INFO_FIELD)
    if isinstance(item_info, list):
        for raw_record in item_info:
            record = _decode_scene_uar_record(raw_record)
            if not record:
                continue
            dependency = _scene_item_dependency_record(
                record,
                dependency_type="scene_item",
                property_name="StudioScene.Item",
                index=index,
            )
            if dependency:
                dependencies.append(dependency)
                index += 1

    pattern_info = metadata.get(SCENE_PATTERN_INFO_FIELD)
    if isinstance(pattern_info, list):
        for raw_pattern in pattern_info:
            pattern = _decode_scene_uar_record(raw_pattern)
            if not pattern:
                continue
            object_pattern_info = pattern.get("ObjectPatternInfo")
            if not isinstance(object_pattern_info, dict):
                continue
            for raw_record in object_pattern_info.values():
                record = _decode_scene_uar_record(raw_record)
                if not record:
                    continue
                dependency = _scene_item_dependency_record(
                    record,
                    dependency_type="scene_pattern",
                    property_name="StudioScene.Pattern",
                    index=index,
                    context=pattern,
                )
                if dependency:
                    dependencies.append(dependency)
                    index += 1
    return dependencies


def inspect_scene_card_bytes(
    file_bytes: bytes,
    *,
    include_character_dependencies: bool = True,
) -> dict[str, Any] | None:
    """Inspect a Studio scene PNG without modifying it.

    Scene map dependencies are stored in the scene-level UAR plugin payload as
    ``mapInfoGUID``. Newer scene cards additionally store object dependencies
    in ``itemInfo`` and ``patternInfo`` rather than the item-oriented ``info``
    records used by character and clothes cards. Embedded ``AIS_Chara``
    objects contribute their own UAR records to the returned dependencies and
    are also summarized separately for the scene-card export action.
    """
    try:
        scene_data = extract_scene_data(file_bytes)
    except (TypeError, ValueError):
        return None
    marker_bytes = STUDIO_SCENE_MARKER.encode("utf-8")
    marker_offset = scene_data.find(marker_bytes)
    if marker_offset < 0:
        return None

    kkex_blocks = _iter_kkex_payloads(scene_data)
    dependencies: list[dict[str, Any]] = []
    plugin_ids: set[str] = set()
    for block in kkex_blocks:
        payload = block["payload"]
        for plugin_id, plugin_payload in payload.items():
            plugin_name = str(plugin_id)
            plugin_ids.add(plugin_name)
            if plugin_name != UNIVERSAL_AUTO_RESOLVER_ID:
                continue
            if not isinstance(plugin_payload, list) or len(plugin_payload) < 2:
                continue
            metadata = plugin_payload[1]
            if not isinstance(metadata, dict):
                continue
            dependency = _scene_dependency_record(
                metadata.get(SCENE_MAP_GUID_FIELD), index=len(dependencies)
            )
            if dependency:
                dependencies.append(dependency)
            dependencies.extend(_scene_uar_dependencies(metadata, len(dependencies)))

    embedded_characters = (
        extract_scene_character_cards(file_bytes)
        if include_character_dependencies
        else []
    )
    # OCIChar objects carry embedded AIS character cards. Their clothing,
    # hair, face and body dependencies live in each character card's UAR
    # payload, not in the scene-level UAR payload parsed above.
    for character in embedded_characters:
        payload = character.get("payload")
        if not isinstance(payload, (bytes, bytearray)):
            continue
        try:
            character_dependencies = extract_auto_resolver_records_from_card_data(
                b"\x00\x00\x00\x00" + bytes(payload)
            )
        except (TypeError, ValueError, IndexError, OverflowError):
            character_dependencies = []
        dependencies.extend(character_dependencies)

    unique_dependencies = []
    seen_dependencies = set()
    for dependency in dependencies:
        dependency_type = str(dependency.get("DependencyType") or "scene")
        mod_id = str(dependency.get("ModID") or "")
        slot = str(dependency.get("Slot") or "")
        local_slot = str(dependency.get("LocalSlot") or "")
        key = (dependency_type, mod_id, slot, local_slot)
        if key in seen_dependencies:
            continue
        seen_dependencies.add(key)
        unique_dependencies.append(dependency)

    return {
        "is_scene_card": True,
        "scene_marker": STUDIO_SCENE_MARKER,
        "scene_marker_offset": marker_offset,
        "kkex_count": len(kkex_blocks),
        "plugin_ids": sorted(plugin_ids),
        "dependencies": [json_safe_msgpack(item) for item in unique_dependencies],
        "dependency_count": len(unique_dependencies),
        "embedded_character_count": len(embedded_characters),
        "embedded_characters": [
            {
                "index": item["index"],
                "name": item.get("name") or "",
                "sex": item.get("sex"),
            }
            for item in embedded_characters
        ],
    }


def inspect_scene_card_file(
    card_path: Path,
    *,
    include_character_dependencies: bool = True,
) -> dict[str, Any] | None:
    try:
        return inspect_scene_card_bytes(
            card_path.read_bytes(),
            include_character_dependencies=include_character_dependencies,
        )
    except OSError:
        return None
