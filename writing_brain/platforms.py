from __future__ import annotations

from typing import Any, Iterable


DEFAULT_PLATFORM = "wechat"

PLATFORM_ALIASES = {
    "wechat": ("wechat", "公众号"),
    "zhihu": ("zhihu", "知乎"),
    "xiaohongshu": ("xiaohongshu", "小红书"),
    "csdn": ("csdn", "CSDN"),
    "juejin": ("juejin", "掘金"),
    "toutiao": ("toutiao", "今日头条", "头条"),
    "renrendoushichanpinjingli": ("renrendoushichanpinjingli", "人人都是产品经理"),
}

PLATFORM_NAMES = {
    "wechat": "公众号",
    "zhihu": "知乎",
    "xiaohongshu": "小红书",
    "csdn": "CSDN",
    "juejin": "掘金",
    "toutiao": "今日头条",
    "renrendoushichanpinjingli": "人人都是产品经理",
}

PLATFORM_FILE_STEMS = {
    "wechat": "公众号版",
    "zhihu": "知乎版",
    "xiaohongshu": "小红书版",
    "csdn": "CSDN版",
    "juejin": "掘金版",
    "toutiao": "头条版",
    "renrendoushichanpinjingli": "人人都是产品经理版",
}


def normalize_platform(value: Any, *, default: str = DEFAULT_PLATFORM) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    lowered = raw.lower()
    for canonical, aliases in PLATFORM_ALIASES.items():
        if lowered in {alias.lower() for alias in aliases}:
            return canonical
    return lowered


def normalize_platform_list(raw: Any, *, default: Iterable[str] | None = None) -> list[str]:
    items = raw if raw is not None else list(default or [])
    deduped: list[str] = []
    for item in items:
        normalized = normalize_platform(item, default="")
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def platform_display_name(value: Any) -> str:
    normalized = normalize_platform(value, default="")
    if not normalized:
        return ""
    return PLATFORM_NAMES.get(normalized, str(value).strip() or normalized)


def platform_file_stem(value: Any) -> str:
    normalized = normalize_platform(value, default="")
    if normalized in PLATFORM_FILE_STEMS:
        return PLATFORM_FILE_STEMS[normalized]
    name = platform_display_name(value)
    return f"{name}版" if name else "未命名平台版"
