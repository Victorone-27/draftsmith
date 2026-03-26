from __future__ import annotations

import json
from pathlib import Path
from typing import Any


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
GENERATED_INDEX_FILENAME = "生成图片索引.json"
PUBLIC_INDEX_FILENAME = "公开图片索引.json"


def build_image_supply_bundle(output_dir: Path) -> dict[str, Any]:
    image_dir = output_dir / "图片"
    placements = _read_json(image_dir / "插图位置.json")
    generated_index = read_image_index(image_dir / GENERATED_INDEX_FILENAME)
    public_index = read_image_index(image_dir / PUBLIC_INDEX_FILENAME)

    slots = [
        _build_slot_record(item, image_dir=image_dir, generated_index=generated_index, public_index=public_index)
        for item in _iter_slots(placements)
    ]
    generated_expected = sum(1 for item in slots if item["expected_source_type"] == "generated")
    public_expected = sum(1 for item in slots if item["expected_source_type"] == "public")
    generated_present = sum(1 for item in slots if item["expected_source_type"] == "generated" and item["file_present"])
    public_present = sum(1 for item in slots if item["expected_source_type"] == "public" and item["file_present"])

    return {
        "placements": placements,
        "slots": slots,
        "generated_index": generated_index,
        "public_index": public_index,
        "summary": {
            "expected_image_slots": len(slots),
            "generated_expected": generated_expected,
            "public_expected": public_expected,
            "generated_present": generated_present,
            "public_present": public_present,
            "images_present": generated_present + public_present,
        },
    }


def read_image_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def write_image_index(path: Path, entries: dict[str, Any]) -> None:
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_slot_file(root: Path, filename: str) -> Path | None:
    for ext in IMAGE_EXTENSIONS:
        candidate = root / f"{filename}{ext}"
        if candidate.exists():
            return candidate
    return None


def _iter_slots(placements: dict[str, Any]) -> list[dict[str, Any]]:
    if not placements:
        return []
    slots: list[dict[str, Any]] = []
    cover = dict(placements.get("cover") or {})
    if cover.get("path"):
        slots.append(
            {
                "slot_type": "cover",
                "filename": Path(str(cover.get("path") or "")).stem,
                "path": str(cover.get("path") or ""),
                "source_type": str(cover.get("source_type") or _root_to_source_type(str(cover.get("path") or ""))),
                "caption": str(cover.get("caption") or ""),
                "role": str(cover.get("role") or "cover_opinion"),
                "allowed_source_types": list(cover.get("allowed_source_types") or [str(cover.get("source_type") or _root_to_source_type(str(cover.get("path") or "")))]),
            }
        )
    for item in placements.get("inline") or []:
        raw_path = str(item.get("path") or "")
        if not raw_path:
            continue
        slots.append(
            {
                "slot_type": "inline",
                "filename": Path(raw_path).stem,
                "path": raw_path,
                "source_type": str(item.get("source_type") or _root_to_source_type(raw_path)),
                "caption": str(item.get("caption") or ""),
                "after_contains": str(item.get("after_contains") or ""),
                "role": str(item.get("role") or "supporting_visual"),
                "allowed_source_types": list(item.get("allowed_source_types") or [str(item.get("source_type") or _root_to_source_type(raw_path))]),
            }
        )
    return slots


def _build_slot_record(
    slot: dict[str, Any],
    *,
    image_dir: Path,
    generated_index: dict[str, Any],
    public_index: dict[str, Any],
) -> dict[str, Any]:
    expected_source_type = str(slot.get("source_type") or "generated").strip().lower()
    filename = str(slot.get("filename") or "").strip()
    root_name = "公开来源" if expected_source_type == "public" else "已生成"
    root_dir = image_dir / root_name
    actual_path = find_slot_file(root_dir, filename)
    if actual_path is None:
        alternate_root = image_dir / ("已生成" if expected_source_type == "public" else "公开来源")
        actual_path = find_slot_file(alternate_root, filename)
    metadata = dict((public_index if expected_source_type == "public" else generated_index).get(filename) or {})
    if not metadata and actual_path is not None:
        actual_source_type = _root_to_source_type(str(actual_path.relative_to(image_dir)))
        metadata = dict((public_index if actual_source_type == "public" else generated_index).get(filename) or {})
    metadata_present = bool(metadata)
    source_url = str(metadata.get("source_url") or "").strip()
    license_name = str(metadata.get("license") or "").strip()
    license_status = str(metadata.get("license_status") or ("verified" if license_name else "unknown")).strip().lower()
    actual_source_type = _root_to_source_type(str(actual_path.relative_to(image_dir))) if actual_path is not None else ""

    if actual_source_type == "public" or (expected_source_type == "public" and not actual_source_type):
        publishable = bool(metadata.get("publishable")) and bool(source_url) and bool(license_name)
    else:
        publishable = bool(actual_path is not None)

    return {
        "slot_type": str(slot.get("slot_type") or ""),
        "filename": filename,
        "caption": str(slot.get("caption") or ""),
        "after_contains": str(slot.get("after_contains") or ""),
        "role": str(slot.get("role") or "supporting_visual"),
        "allowed_source_types": [str(item) for item in slot.get("allowed_source_types") or [] if str(item).strip()],
        "expected_source_type": expected_source_type,
        "expected_path": str(slot.get("path") or ""),
        "file_present": actual_path is not None,
        "actual_path": str(actual_path) if actual_path is not None else "",
        "actual_source_type": actual_source_type,
        "metadata_present": metadata_present,
        "metadata": metadata,
        "source_url": source_url,
        "license": license_name,
        "license_status": license_status,
        "provider": str(metadata.get("provider") or "").strip(),
        "model": str(metadata.get("model") or "").strip(),
        "publishable": publishable,
        "publishability_reason": str(metadata.get("publishability_reason") or "").strip(),
    }


def _root_to_source_type(raw_path: str) -> str:
    path = Path(raw_path)
    root_name = path.parts[0] if path.parts else "已生成"
    return "public" if root_name == "公开来源" else "generated"


def _read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
