import struct
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from star_manager.core.scene_card import extract_scene_character_cards, inspect_scene_card_bytes


def _fixstr(value: str) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) < 32:
        return bytes([0xA0 | len(encoded)]) + encoded
    assert len(encoded) < 256
    return b"\xD9" + bytes([len(encoded)]) + encoded


def _scene_png(payload: bytes) -> bytes:
    png = b"\x89PNG\r\n\x1a\n"
    png += struct.pack(">I", 0) + b"IEND" + b"\xaeB`\x82"
    marker = "【StudioNEOV2】".encode("utf-8")
    envelope = (
        _fixstr("KKEx")[1:]
        + (3).to_bytes(4, "little")
        + len(payload).to_bytes(4, "little")
        + payload
    )
    return png + bytes([len(marker)]) + marker + envelope


def _pack(value) -> bytes:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) < 32:
            return bytes([0xA0 | len(encoded)]) + encoded
        return b"\xD9" + bytes([len(encoded)]) + encoded
    if isinstance(value, bytes):
        if len(value) >= 256:
            raise AssertionError("test helper only supports bin8")
        return b"\xC4" + bytes([len(value)]) + value
    if isinstance(value, int):
        if 0 <= value < 128:
            return bytes([value])
        if 0 <= value < 256:
            return b"\xCC" + bytes([value])
        if 0 <= value < 2**32:
            return b"\xCE" + value.to_bytes(4, "big")
        raise AssertionError("test helper only supports non-negative uint32")
    if isinstance(value, list):
        if len(value) >= 16:
            raise AssertionError("test helper only supports fixarray")
        return bytes([0x90 | len(value)]) + b"".join(_pack(item) for item in value)
    if isinstance(value, dict):
        if len(value) >= 16:
            raise AssertionError("test helper only supports fixmap")
        return bytes([0x80 | len(value)]) + b"".join(
            _pack(key) + _pack(item) for key, item in value.items()
        )
    raise AssertionError(f"unsupported test value: {value!r}")


def test_scene_card_extracts_map_guid_from_scene_level_uar():
    plugin = "com.bepis.sideloader.universalautoresolver"
    payload = (
        bytes([0x81])
        + _fixstr(plugin)
        + bytes([0x92, 0x00, 0x81])
        + _fixstr("mapInfoGUID")
        + _fixstr("com.example.schoolpool")
    )
    parsed = inspect_scene_card_bytes(_scene_png(payload))

    assert parsed is not None
    assert parsed["scene_marker"] == "【StudioNEOV2】"
    assert parsed["dependency_count"] == 1
    assert parsed["dependencies"][0]["ModID"] == "com.example.schoolpool"


def test_scene_card_extracts_item_and_pattern_info_records():
    plugin = "com.bepis.sideloader.universalautoresolver"
    item = {
        "ModID": "com.example.props",
        "Slot": 3,
        "LocalSlot": 100003892,
        "Category": 0,
        "SceneDicKey": 731,
        "SceneObjectOrder": 0,
    }
    pattern = {
        "SceneDicKey": 2384,
        "SceneObjectOrder": 1829,
        "ObjectPatternInfo": {
            "0": {
                "GUID": "alex7997.patterns",
                "Slot": 184,
                "LocalSlot": 100002838,
            }
        },
    }
    metadata = {
        "itemInfo": [_pack(item), _pack(item)],
        "patternInfo": [pattern],
    }
    payload = _pack({plugin: [0, metadata]})
    parsed = inspect_scene_card_bytes(_scene_png(payload))

    assert parsed is not None
    assert parsed["dependency_count"] == 2
    assert {(item["DependencyType"], item["ModID"]) for item in parsed["dependencies"]} == {
        ("scene_item", "com.example.props"),
        ("scene_pattern", "alex7997.patterns"),
    }
    item_dependency = next(item for item in parsed["dependencies"] if item["DependencyType"] == "scene_item")
    assert item_dependency["CategoryNo"] == 0
    assert item_dependency["Slot"] == 3
    pattern_dependency = next(item for item in parsed["dependencies"] if item["DependencyType"] == "scene_pattern")
    assert pattern_dependency["SceneDicKey"] == 2384
    assert pattern_dependency["SceneObjectOrder"] == 1829


def test_scene_card_extracts_embedded_ais_character_payload():
    marker = "【AIS_Chara】"

    def dotnet_string(value: str) -> bytes:
        encoded = value.encode("utf-8")
        return bytes([len(encoded)]) + encoded

    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 0) + b"IEND" + b"\xaeB`\x82"
    embedded = (
        (100).to_bytes(4, "little", signed=True)
        + dotnet_string(marker)
        + dotnet_string("1.0")
        + (0).to_bytes(4, "little", signed=True)
        + dotnet_string("user")
        + dotnet_string("card")
        + (2).to_bytes(4, "little", signed=True)
        + b"\x90\x00"
        + (3).to_bytes(8, "little", signed=True)
        + b"abc"
    )

    cards = extract_scene_character_cards(png + b"scene-data" + embedded)

    assert len(cards) == 1
    assert cards[0]["payload"] == embedded


def test_scene_card_includes_embedded_character_dependencies():
    marker = "【AIS_Chara】"

    def dotnet_string(value: str) -> bytes:
        encoded = value.encode("utf-8")
        return bytes([len(encoded)]) + encoded

    record = _pack(
        {
            "ModID": "com.example.character-clothes",
            "CategoryNo": 140,
            "Slot": 3,
            "Property": "outfit.ChaFileClothes.clothestop",
        }
    )
    plugin_id = "com.bepis.sideloader.universalautoresolver"
    kkex = _pack(plugin_id) + _pack([0, {"info": [record]}])
    block_table = _pack(
        {"lstInfo": [{"name": "KKEx", "pos": 0, "size": len(kkex)}]}
    )
    embedded = (
        (100).to_bytes(4, "little", signed=True)
        + dotnet_string(marker)
        + dotnet_string("1.0")
        + (0).to_bytes(4, "little", signed=True)
        + dotnet_string("user")
        + dotnet_string("card")
        + len(block_table).to_bytes(4, "little", signed=True)
        + block_table
        + len(kkex).to_bytes(8, "little", signed=True)
        + kkex
    )

    with patch(
        "star_manager.core.scene_card.extract_scene_character_cards",
        return_value=[{"index": 0, "payload": embedded}],
    ):
        parsed = inspect_scene_card_bytes(_scene_png(b""))

    assert parsed is not None
    assert parsed["dependency_count"] == 1
    assert parsed["dependencies"][0]["ModID"] == "com.example.character-clothes"
    assert parsed["dependencies"][0]["CategoryNo"] == 140
