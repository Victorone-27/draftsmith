from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from docx.image.image import Image as DocxImage

from .config import AppConfig
from .image_review import build_image_review_report
from .image_supply import PUBLIC_INDEX_FILENAME, read_image_index, write_image_index
from .publish import _cleanup_slot_variants, build_publish_pack


COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def collect_public_images(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    output_dir_raw = str(payload.get("output_dir") or payload.get("publish_pack_output_dir") or "").strip()
    output_root_raw = str(payload.get("output_root") or payload.get("release_cycle_output_root") or "").strip()
    release_cycle_result = dict(payload.get("release_cycle_result") or {})

    if release_cycle_result:
        output_root = Path(output_root_raw).expanduser() if output_root_raw else None
        return _collect_for_release_root(payload, config, output_root=output_root, release_cycle_result=release_cycle_result)
    if output_dir_raw:
        return _collect_for_single_pack(payload, config, output_dir=Path(output_dir_raw).expanduser())
    raise ValueError("output_dir 或 release_cycle_result 至少要提供一个")


def _collect_for_release_root(
    payload: dict[str, Any],
    config: AppConfig,
    *,
    output_root: Path | None,
    release_cycle_result: dict[str, Any],
) -> dict[str, Any]:
    platform_results = list(release_cycle_result.get("platform_results") or [])
    resolved_output_root = output_root
    if resolved_output_root is None:
        raw = str(release_cycle_result.get("output_root") or "").strip()
        resolved_output_root = Path(raw).expanduser() if raw else Path()
    results: list[dict[str, Any]] = []
    artifact_refs: list[str] = []
    actions: list[str] = []

    for platform_result in platform_results:
        publish_result = dict(platform_result.get("publish_result") or {})
        pack_output_dir = str(publish_result.get("output_dir") or "").strip()
        article_path = str(platform_result.get("article_path") or "").strip()
        article_markdown = _resolve_article_markdown(
            {
                **payload,
                "article_path": article_path,
            }
        )
        if not pack_output_dir:
            continue
        collected = _collect_for_single_pack(
            {
                **payload,
                "output_dir": pack_output_dir,
                "article_markdown": article_markdown,
                "platform": str(platform_result.get("platform") or ""),
            },
            config,
            output_dir=Path(pack_output_dir),
        )
        results.append(collected)
        artifact_refs.extend(str(item) for item in collected.get("artifact_refs") or [])
        actions.extend(str(item) for item in collected.get("recommended_next_actions") or [])

    if not results:
        status = "skipped"
    elif any(item.get("status") in {"failed", "partial"} for item in results):
        status = "partial"
    elif all(item.get("status") == "skipped" for item in results):
        status = "skipped"
    else:
        status = "completed"
    return {
        "contract_name": "public_image_collect_result",
        "contract_version": "v1",
        "scope": "release_cycle",
        "output_root": str(resolved_output_root),
        "status": status,
        "platform_results": results,
        "artifact_refs": list(dict.fromkeys(artifact_refs)),
        "recommended_next_actions": list(dict.fromkeys(actions)),
    }


def _collect_for_single_pack(payload: dict[str, Any], config: AppConfig, *, output_dir: Path) -> dict[str, Any]:
    image_dir = output_dir / "图片"
    public_dir = image_dir / "公开来源"
    public_dir.mkdir(parents=True, exist_ok=True)
    leads_path = image_dir / "公开图片线索.md"
    sources_path = image_dir / "图片与数据来源.md"
    index_path = image_dir / PUBLIC_INDEX_FILENAME

    slots = _parse_public_leads(leads_path)
    existing_index = read_image_index(index_path)
    results: list[dict[str, Any]] = []
    for slot in slots:
        try:
            result = _fetch_slot_image(slot=slot, public_dir=public_dir, existing_index=existing_index)
        except Exception as exc:
            result = {
                "filename": slot["filename"],
                "status": "failed",
                "error": str(exc),
                "query": slot["query"],
            }
        results.append(result)

    _write_public_index(path=index_path, existing_index=existing_index, results=results)
    refresh_result = _maybe_refresh_publish_pack(payload, config, output_dir=output_dir)
    _update_sources_markdown(sources_path, results)
    image_review_report = build_image_review_report(
        {
            "output_dir": str(output_dir),
            "platform": str(payload.get("platform") or ""),
            "article_markdown": _resolve_article_markdown(payload),
            "title": str(payload.get("title") or ""),
            "topic": str(payload.get("topic") or ""),
            "context_pack": dict(payload.get("context_pack") or {}),
            "use_image_governance_review": payload.get("use_image_governance_review"),
        }
    )
    artifact_refs = [str(index_path), str(sources_path)]
    artifact_refs.extend(str(item["path"]) for item in results if item.get("status") in {"ok", "existing"} and item.get("path"))
    artifact_refs.extend(str(item) for item in (refresh_result or {}).get("artifact_refs") or [])
    artifact_refs.extend(str(item) for item in image_review_report.get("artifact_refs") or [])

    success_count = sum(1 for item in results if item.get("status") in {"ok", "existing"})
    downloaded_count = sum(1 for item in results if item.get("status") == "ok")
    reused_count = sum(1 for item in results if item.get("status") == "existing")
    if not results:
        status = "skipped"
    elif success_count == len(results):
        status = "completed"
    elif success_count:
        status = "partial"
    else:
        status = "failed"
    actions = list(image_review_report.get("required_actions") or [])
    if downloaded_count:
        actions.insert(0, f"已自动补到 {downloaded_count} 张公共来源图片，并刷新发布包。")
    elif reused_count:
        actions.insert(0, f"已复用 {reused_count} 张已存在的公共来源图片，并刷新发布包。")
    if refresh_result and refresh_result.get("status") == "skipped":
        actions.insert(0, "已抓取图片，但由于缺少 article_markdown，未自动刷新 Word 文档。")
    if not results:
        actions.insert(0, "当前没有需要联网检索的公共图片槽位。")

    return {
        "contract_name": "public_image_collect_result",
        "contract_version": "v1",
        "scope": "publish_pack",
        "output_dir": str(output_dir),
        "status": status,
        "download_results": results,
        "refresh_result": refresh_result,
        "image_review_report": image_review_report,
        "artifact_refs": list(dict.fromkeys(artifact_refs)),
        "recommended_next_actions": list(dict.fromkeys(actions)),
    }


def _parse_public_leads(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    slots: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if current:
                slots.append(current)
            current = {"filename": stripped[3:].strip(), "query": ""}
            continue
        if current and stripped.startswith("- 公开渠道搜索词："):
            current["query"] = stripped.removeprefix("- 公开渠道搜索词：").strip()
    if current:
        slots.append(current)
    return [slot for slot in slots if slot.get("filename") and slot.get("query")]


def _fetch_slot_image(*, slot: dict[str, str], public_dir: Path, existing_index: dict[str, Any]) -> dict[str, Any]:
    filename = slot["filename"]
    query = slot["query"]
    existing_path = _existing_image_path(public_dir, filename)
    if existing_path is not None and _path_is_docx_compatible(existing_path):
        _cleanup_slot_variants(public_dir, filename, keep_path=existing_path)
        known = dict(existing_index.get(filename) or {})
        return {
            "filename": filename,
            "status": "existing",
            "path": str(existing_path),
            "query": query,
            "title": str(known.get("title") or filename),
            "source_url": str(known.get("source_url") or ""),
            "download_url": str(known.get("download_url") or ""),
            "license": str(known.get("license") or ""),
            "author": str(known.get("author") or ""),
            "license_status": str(known.get("license_status") or ""),
            "publishable": bool(known.get("publishable", False)),
            "publishability_reason": str(known.get("publishability_reason") or ""),
        }

    chosen: dict[str, str] | None = None
    used_query = query
    for candidate_query in _query_variants_for_commons(query):
        candidates = _search_public_sources(candidate_query)
        if not candidates:
            continue
        filtered = _filter_relevant_candidates(candidates, slot=slot, query=candidate_query)
        if not filtered:
            continue
        chosen = filtered[0]
        used_query = candidate_query
        break
    if not chosen:
        return {
            "filename": filename,
            "status": "failed",
            "error": "no_candidate_found",
            "query": query,
        }

    raw = _download_bytes(chosen["download_url"])
    extension = _guess_extension(chosen["download_url"], raw)
    raw, extension = _normalize_docx_compatible_image(raw, extension)
    path = public_dir / f"{filename}.{extension}"
    path.write_bytes(raw)
    _cleanup_slot_variants(public_dir, filename, keep_path=path)
    return {
        "filename": filename,
        "status": "ok",
        "path": str(path),
        "query": used_query,
        "title": chosen["title"],
        "source_url": chosen["page_url"],
        "download_url": chosen["download_url"],
        "license": chosen.get("license", ""),
        "author": chosen.get("author", ""),
        "license_status": "verified" if chosen.get("license") else "unknown",
        "publishable": bool(chosen.get("page_url") and chosen.get("license")),
        "publishability_reason": _publishability_reason(chosen),
    }


def _publishability_reason(candidate: dict[str, str]) -> str:
    if not candidate.get("page_url") or not candidate.get("license"):
        return "missing_license_or_source"
    license_lower = candidate.get("license", "").lower()
    if "unsplash" in license_lower:
        return "unsplash_license"
    if "pexels" in license_lower:
        return "pexels_license"
    return "wikimedia_commons_with_license"


def _query_variants_for_commons(query: str) -> list[str]:
    raw = str(query or "").strip()
    variants: list[str] = []
    if raw:
        variants.append(raw)

    compact = _compact_commons_query(raw)
    if compact and compact not in variants:
        variants.append(compact)

    generic = _generic_commons_query(raw)
    if generic and generic not in variants:
        variants.append(generic)

    return variants


def _compact_commons_query(query: str) -> str:
    normalized = re.sub(r"[*_`#>\[\]\(\)]+", " ", query or "")
    normalized = re.sub(r"[，。；：、“”‘’（）()]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    keywords: list[str] = []
    for token in [
        "AI",
        "人工智能",
        "漫剧",
        "动画",
        "漫画",
        "视频",
        "工作流",
        "内容生产",
        "模型",
        "Prompt",
        "提示词",
        "角色",
        "场景",
        "团队",
        "协作",
        "工业化",
    ]:
        if token.lower() in normalized.lower() and token not in keywords:
            keywords.append(token)
    if not keywords:
        words = [word for word in normalized.split(" ") if 1 < len(word) <= 12]
        keywords.extend(words[:4])
    suffix = ["真实照片"]
    if any(token in normalized for token in ["AI", "人工智能", "模型", "Prompt", "提示词"]):
        suffix.extend(["会议", "现场"])
    elif any(token in normalized for token in ["漫剧", "动画", "漫画", "视频"]):
        suffix.extend(["制作", "现场"])
    else:
        suffix.extend(["团队", "现场"])
    return " ".join([*keywords[:4], *suffix]).strip()[:72]


def _generic_commons_query(query: str) -> str:
    normalized = str(query or "").lower()
    if any(token in normalized for token in ["老板", "预算", "企业", "管理", "协同", "流程", "组织", "business", "executive", "meeting", "teamwork"]):
        return "business leaders round table meeting"
    if any(token in normalized for token in ["prompt", "提示词", "模型", "人工智能", "ai", "agent", "智能体", "technology"]):
        return "business meeting technology leaders"
    if any(token in normalized for token in ["团队", "协作", "工作流", "team", "office"]):
        return "businesspeople meeting office"
    if any(token in normalized for token in ["漫剧", "动画", "漫画", "视频"]):
        return "animation production studio"
    return "business office"


def _search_commons(query: str) -> list[dict[str, str]]:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "5",
        "prop": "imageinfo|info",
        "inprop": "url",
        "iiprop": "url|extmetadata",
        "iiurlwidth": "1600",
        "format": "json",
        "origin": "*",
    }
    url = f"{COMMONS_API}?{urlencode(params)}"
    body = _request_json(url)
    raw_pages = (body.get("query") or {}).get("pages") or {}
    if isinstance(raw_pages, list):
        pages = raw_pages
    else:
        pages = list(raw_pages.values())
    results: list[dict[str, str]] = []
    for page in pages:
        imageinfo = ((page.get("imageinfo") or [{}])[0]) if isinstance(page, dict) else {}
        extmeta = imageinfo.get("extmetadata") or {}
        download_url = str(imageinfo.get("thumburl") or imageinfo.get("url") or "").strip()
        page_url = str(page.get("fullurl") or imageinfo.get("descriptionurl") or "").strip()
        if not download_url or not page_url:
            continue
        results.append(
            {
                "title": str(page.get("title") or "").strip(),
                "page_url": page_url,
                "download_url": download_url,
                "license": _extmeta_value(extmeta, "LicenseShortName"),
                "author": _extmeta_value(extmeta, "Artist"),
            }
        )
    return results


def _search_unsplash(query: str) -> list[dict[str, str]]:
    access_key = (os.environ.get("UNSPLASH_ACCESS_KEY") or "").strip()
    if not access_key:
        return []
    params = urlencode({"query": query, "per_page": "5", "orientation": "landscape"})
    url = f"https://api.unsplash.com/search/photos?{params}"
    body = _request_json(url, headers={"Authorization": f"Client-ID {access_key}"})
    results: list[dict[str, str]] = []
    for item in body.get("results") or []:
        download_url = str((item.get("urls") or {}).get("regular") or "").strip()
        page_url = str((item.get("links") or {}).get("html") or "").strip()
        if not download_url or not page_url:
            continue
        results.append({
            "title": str(item.get("alt_description") or "").strip(),
            "page_url": page_url,
            "download_url": download_url,
            "license": "Unsplash License",
            "author": str((item.get("user") or {}).get("name") or "").strip(),
        })
    return results


def _search_pexels(query: str) -> list[dict[str, str]]:
    api_key = (os.environ.get("PEXELS_API_KEY") or "").strip()
    if not api_key:
        return []
    params = urlencode({"query": query, "per_page": "5", "orientation": "landscape"})
    url = f"https://api.pexels.com/v1/search?{params}"
    body = _request_json(url, headers={"Authorization": api_key})
    results: list[dict[str, str]] = []
    for item in body.get("photos") or []:
        download_url = str((item.get("src") or {}).get("large") or "").strip()
        page_url = str(item.get("url") or "").strip()
        if not download_url or not page_url:
            continue
        results.append({
            "title": str(item.get("alt") or "").strip(),
            "page_url": page_url,
            "download_url": download_url,
            "license": "Pexels License",
            "author": str(item.get("photographer") or "").strip(),
        })
    return results


def _search_public_sources(query: str) -> list[dict[str, str]]:
    for search_fn in (_search_unsplash, _search_pexels, _search_commons):
        try:
            results = search_fn(query)
            if results:
                return results
        except Exception:
            continue
    return []


def _filter_relevant_candidates(candidates: list[dict[str, str]], *, slot: dict[str, str], query: str) -> list[dict[str, str]]:
    scored: list[tuple[int, dict[str, str]]] = []
    slot_text = f"{slot.get('filename') or ''} {slot.get('query') or ''} {query}".lower()
    required_tokens = _required_visual_tokens(slot_text)

    for candidate in candidates:
        title = str(candidate.get("title") or "").lower()
        page_url = str(candidate.get("page_url") or "").lower()
        penalty_terms = [
            ".pdf",
            ".webm",
            "document",
            "notice",
            "通知",
            "方案",
            "办公室",
            "page ",
            "page-",
            "diagram",
            "chart",
            "map",
            "logo",
            "icon",
            "text",
            "scan",
            "baseball",
            "football",
            "basketball",
            "soccer",
            "school",
            "high school",
            "university",
            "students",
            "symbol",
            "emblem",
            "flag",
            "building",
            "house",
            "architecture",
        ]
        if any(term in title or term in page_url for term in penalty_terms):
            continue

        score = 0
        positive_terms = [
            ("business", 2),
            ("office", 2),
            ("meeting", 2),
            ("team", 2),
            ("people", 1),
            ("computer", 1),
            ("conference", 1),
            ("technology", 1),
            ("artificial intelligence", 2),
            ("robot", 1),
            ("business", 2),
            ("executive", 2),
            ("manager", 1),
        ]
        for term, weight in positive_terms:
            if term in title:
                score += weight

        if required_tokens and not any(token in title for token in required_tokens):
            continue
        scored.append((score, candidate))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in scored]


def _required_visual_tokens(text: str) -> list[str]:
    mapping = [
        (["老板", "预算", "企业", "管理"], ["business", "office", "meeting", "企业", "管理", "商务"]),
        (["agent", "ai", "人工智能", "智能体"], ["technology", "artificial intelligence", "robot", "computer", "人工智能", "ai", "智能"]),
        (["团队", "协作", "跨部门", "组织"], ["team", "meeting", "office", "团队", "协作", "组织"]),
    ]
    tokens: list[str] = []
    for triggers, values in mapping:
        if any(trigger.lower() in text for trigger in triggers):
            for value in values:
                if value not in tokens:
                    tokens.append(value)
    return tokens


def _request_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    merged = {"User-Agent": "writing-brain/0.1"}
    if headers:
        merged.update(headers)
    request = Request(url, headers=merged)
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "writing-brain/0.1"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def _extmeta_value(extmeta: dict[str, Any], key: str) -> str:
    value = dict(extmeta.get(key) or {}).get("value")
    text = str(value or "").strip()
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _guess_extension(url: str, raw: bytes) -> str:
    lowered = url.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if ext in lowered:
            return ext.lstrip(".").replace("jpeg", "jpg")
    if raw.startswith(b"\x89PNG"):
        return "png"
    if raw.startswith(b"\xff\xd8"):
        return "jpg"
    if raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
        return "webp"
    return "jpg"


def _existing_image_path(public_dir: Path, filename: str) -> Path | None:
    for ext in ("png", "jpg", "jpeg", "webp"):
        candidate = public_dir / f"{filename}.{ext}"
        if candidate.exists():
            return candidate
    return None


def _normalize_docx_compatible_image(raw: bytes, extension: str) -> tuple[bytes, str]:
    normalized_extension = extension.strip().lower().replace("jpeg", "jpg")
    if _bytes_are_docx_compatible(raw, normalized_extension):
        return raw, normalized_extension

    converted = _convert_image_with_sips(raw, normalized_extension)
    if converted is not None and _bytes_are_docx_compatible(converted, "png"):
        return converted, "png"

    return raw, normalized_extension


def _path_is_docx_compatible(path: Path) -> bool:
    try:
        DocxImage.from_file(str(path))
        return True
    except Exception:
        return False


def _bytes_are_docx_compatible(raw: bytes, extension: str) -> bool:
    suffix = "." + extension.lstrip(".")
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(raw)
        tmp.flush()
        return _path_is_docx_compatible(Path(tmp.name))


def _convert_image_with_sips(raw: bytes, extension: str) -> bytes | None:
    if shutil.which("sips") is None:
        return None

    src_suffix = "." + extension.lstrip(".")
    src_path: Path | None = None
    dst_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=src_suffix, delete=False) as src:
            src.write(raw)
            src.flush()
            src_path = Path(src.name)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as dst:
            dst_path = Path(dst.name)
        completed = subprocess.run(
            ["sips", "-s", "format", "png", str(src_path), "--out", str(dst_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if completed.returncode != 0 or dst_path is None or not dst_path.exists():
            return None
        return dst_path.read_bytes()
    except Exception:
        return None
    finally:
        for path in [src_path, dst_path]:
            if path is not None and path.exists():
                try:
                    path.unlink()
                except Exception:
                    pass


def _write_public_index(*, path: Path, existing_index: dict[str, Any], results: list[dict[str, Any]]) -> None:
    index = dict(existing_index)
    for item in results:
        if item.get("status") not in {"ok", "existing"} or not item.get("path"):
            continue
        index[str(item.get("filename") or "")] = {
            "path": str(item.get("path") or ""),
            "title": str(item.get("title") or ""),
            "source_url": str(item.get("source_url") or ""),
            "download_url": str(item.get("download_url") or ""),
            "license": str(item.get("license") or ""),
            "author": str(item.get("author") or ""),
            "license_status": str(item.get("license_status") or ("verified" if item.get("license") else "unknown")),
            "publishable": bool(item.get("publishable", False)),
            "publishability_reason": str(item.get("publishability_reason") or ""),
        }
    write_image_index(path, index)


def _update_sources_markdown(path: Path, results: list[dict[str, Any]]) -> None:
    if not path.exists():
        base = "# 图片与数据来源\n"
    else:
        base = path.read_text(encoding="utf-8")
    marker = "\n## 自动检索公开图源\n"
    if marker in base:
        base = base.split(marker, 1)[0].rstrip() + "\n"
    lines = [base.rstrip(), "", "## 自动检索公开图源", ""]
    if not results:
        lines.append("- 本轮没有执行公共图片检索。")
    else:
        for item in results:
            if item.get("status") == "failed":
                lines.append(f"- {item.get('filename')}：检索失败（{item.get('error')}）")
                continue
            line = f"- {item.get('filename')}：{item.get('title') or item.get('filename')}"
            if item.get("source_url"):
                line += f"，{item.get('source_url')}"
            elif item.get("path"):
                line += f"，已存在本地文件：{item.get('path')}"
            if item.get("license"):
                line += f"，许可：{item.get('license')}"
            if item.get("author"):
                line += f"，作者：{item.get('author')}"
            lines.append(line)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _maybe_refresh_publish_pack(
    payload: dict[str, Any],
    config: AppConfig,
    *,
    output_dir: Path,
) -> dict[str, Any] | None:
    if not bool(payload.get("refresh_publish_pack", True)):
        return {
            "status": "skipped",
            "reason": "refresh_disabled",
            "artifact_refs": [],
        }

    article_markdown = _resolve_article_markdown(payload)
    if not article_markdown:
        return {
            "status": "skipped",
            "reason": "missing_article_markdown",
            "artifact_refs": [],
        }

    result = build_publish_pack(
        {
            **payload,
            "output_dir": str(output_dir),
            "article_markdown": article_markdown,
        },
        config,
    )
    return {
        "status": "completed",
        "artifact_refs": [str(item) for item in result.get("artifact_refs") or []],
        "publish_result": result,
    }


def _resolve_article_markdown(payload: dict[str, Any]) -> str:
    direct = str(
        payload.get("article_markdown")
        or payload.get("final_article_markdown")
        or payload.get("draft_text")
        or ""
    ).strip()
    if direct:
        return direct
    article_path = str(payload.get("article_path") or "").strip()
    if article_path and Path(article_path).exists():
        return Path(article_path).read_text(encoding="utf-8")
    return ""
