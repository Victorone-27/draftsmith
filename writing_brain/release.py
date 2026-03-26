from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import AppConfig, ensure_runtime_dirs
from .context_pack import build_context_pack
from .publish import PLATFORM_NAMES, build_publish_pack
from .review import build_review_report
from .text import compact_whitespace, count_evidence_signals, now_run_id, slug_for_filename, split_paragraphs
from .revision import resolve_article_text
from .writer import run_writer_turn


DEFAULT_RELEASE_PLATFORMS = [
    "wechat",
    "zhihu",
    "xiaohongshu",
    "renrendoushichanpinjingli",
    "toutiao",
    "csdn",
    "juejin",
]
DEFAULT_PLATFORM_TARGET_CHARS = 2000
MAX_PLATFORM_EXPANSION_ROUNDS = 2


def run_release_cycle(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    ensure_runtime_dirs(config)
    run_id = str(payload.get("run_id") or now_run_id("release"))
    article_markdown = str(
        payload.get("article_markdown")
        or payload.get("final_article_markdown")
        or payload.get("draft_text")
        or ""
    ).strip()
    if not article_markdown:
        raise ValueError("article_markdown 不能为空")

    source_context = dict(payload.get("context_pack") or {})
    source_platform = str(payload.get("source_platform") or source_context.get("platform") or "wechat").strip().lower()
    topic = str(payload.get("topic") or source_context.get("topic") or _extract_title(article_markdown)).strip()
    platforms = _normalize_platforms(payload.get("platforms"))
    output_root = _resolve_release_root(payload, config, topic)
    drafts_dir = output_root / "平台稿"
    packs_dir = output_root / "投稿包"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    packs_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    manual_platform_articles = {
        str(key).strip().lower(): str(value).strip()
        for key, value in dict(payload.get("manual_platform_articles") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    manual_revised_platform_articles = {
        str(key).strip().lower(): str(value).strip()
        for key, value in dict(payload.get("manual_revised_platform_articles") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    for platform in platforms:
        platform_context = build_context_pack(
            {
                "run_id": f"{run_id}_{platform}",
                "topic": topic,
                "platform": platform,
                "user_goal": str(payload.get("user_goal") or source_context.get("user_goal") or "").strip(),
                "audience": str(payload.get("audience") or source_context.get("audience") or "").strip(),
                "tone_target": str(payload.get("tone_target") or source_context.get("tone_target") or "锋利但克制").strip(),
                "must_cover_points": list(source_context.get("must_cover_points") or payload.get("must_cover_points") or []),
                "style_rules": list(source_context.get("style_rules") or payload.get("style_rules") or []),
                "platform_rules": list(payload.get("platform_rule_overrides") or []),
            },
            config,
        )

        platform_name = PLATFORM_NAMES.get(platform, platform)
        platform_run_id = f"{run_id}_{platform}"
        target_chars = _platform_target_chars(platform, payload)
        draft_turn: dict[str, Any] | None = None
        draft_source = "source_article"
        platform_article = article_markdown
        manual_platform_article = manual_platform_articles.get(platform, "")

        if manual_platform_article:
            platform_article = manual_platform_article
            draft_source = "manual_input"
        elif platform != source_platform or bool(payload.get("force_rewrite_all_platforms", False)):
            draft_turn = run_writer_turn(
                {
                    "run_id": platform_run_id,
                    "task_mode": "draft",
                    "context_pack": platform_context,
                    "current_draft": article_markdown,
                    "user_message": _platform_adaptation_message(
                        platform,
                        source_platform=source_platform,
                        target_chars=target_chars,
                    ),
                },
                config,
            )
            platform_article, draft_source = resolve_article_text(
                writer_turn=draft_turn,
                manual_text="",
            )
            if not platform_article:
                platform_article = article_markdown
                draft_source = "source_article"

        review_report = build_review_report(
            {
                "run_id": platform_run_id,
                "review_id": f"review_{platform_run_id}",
                "draft_text": platform_article,
                "context_pack": platform_context,
                "platform": platform,
                "use_model_reviewer": bool(payload.get("use_model_reviewer", False)),
            }
        )
        article_assessment = _assess_platform_article(
            article_markdown=platform_article,
            source_article_markdown=article_markdown,
            platform=platform,
            target_chars=target_chars,
        )

        revise_turn: dict[str, Any] | None = None
        expansion_turns: list[dict[str, Any]] = []
        revised_source = "none"
        manual_revised_platform_article = manual_revised_platform_articles.get(platform, "")
        if manual_revised_platform_article:
            platform_article = manual_revised_platform_article
            revised_source = "manual_input"
            review_report = build_review_report(
                {
                    "run_id": platform_run_id,
                    "review_id": f"review_{platform_run_id}_revised",
                    "draft_text": platform_article,
                    "context_pack": platform_context,
                    "platform": platform,
                    "use_model_reviewer": bool(payload.get("use_model_reviewer", False)),
                }
            )
            article_assessment = _assess_platform_article(
                article_markdown=platform_article,
                source_article_markdown=article_markdown,
                platform=platform,
                target_chars=target_chars,
            )
        elif bool(payload.get("auto_revise", True)) and review_report.get("decision") != "pass":
            revise_turn = run_writer_turn(
                {
                    "run_id": platform_run_id,
                    "task_mode": "revise",
                    "context_pack": platform_context,
                    "current_draft": platform_article,
                    "review_report": review_report,
                    "user_message": "请根据 reviewer 意见定向修稿，同时保留该平台语气和结构习惯。",
                },
                config,
            )
            revised_text, revised_source = resolve_article_text(
                writer_turn=revise_turn,
                manual_text="",
            )
            if revised_text:
                platform_article = revised_text
                review_report = build_review_report(
                    {
                        "run_id": platform_run_id,
                        "review_id": f"review_{platform_run_id}_revised",
                        "draft_text": platform_article,
                        "context_pack": platform_context,
                        "platform": platform,
                        "use_model_reviewer": bool(payload.get("use_model_reviewer", False)),
                    }
                )
                article_assessment = _assess_platform_article(
                    article_markdown=platform_article,
                    source_article_markdown=article_markdown,
                    platform=platform,
                    target_chars=target_chars,
                )

        if bool(payload.get("auto_revise", True)) and draft_source != "manual_input" and revised_source != "manual_input":
            for expansion_index in range(MAX_PLATFORM_EXPANSION_ROUNDS):
                if article_assessment["is_complete"]:
                    break
                expansion_turn = run_writer_turn(
                    {
                        "run_id": platform_run_id,
                        "task_mode": "revise",
                        "context_pack": platform_context,
                        "current_draft": platform_article,
                        "review_report": review_report,
                        "user_message": _platform_expansion_message(
                            platform=platform,
                            assessment=article_assessment,
                            round_index=expansion_index,
                        ),
                    },
                    config,
                )
                expansion_turns.append(expansion_turn)
                expanded_text, expanded_source = resolve_article_text(
                    writer_turn=expansion_turn,
                    manual_text="",
                )
                if not expanded_text:
                    break
                platform_article = expanded_text
                revised_source = expanded_source or revised_source
                review_report = build_review_report(
                    {
                        "run_id": platform_run_id,
                        "review_id": f"review_{platform_run_id}_expanded_{expansion_index + 1}",
                        "draft_text": platform_article,
                        "context_pack": platform_context,
                        "platform": platform,
                        "use_model_reviewer": bool(payload.get("use_model_reviewer", False)),
                    }
                )
                article_assessment = _assess_platform_article(
                    article_markdown=platform_article,
                    source_article_markdown=article_markdown,
                    platform=platform,
                    target_chars=target_chars,
                )

        review_report = _apply_completeness_guard(
            review_report,
            platform=platform,
            assessment=article_assessment,
        )

        article_path = drafts_dir / f"{platform_name}.md"
        article_path.write_text(platform_article + ("\n" if not platform_article.endswith("\n") else ""), encoding="utf-8")

        publish_result = build_publish_pack(
            {
                "title": _extract_title(platform_article),
                "topic": topic,
                "platform": platform,
                "context_pack": platform_context,
                "article_markdown": platform_article,
                "output_dir": str(packs_dir / platform_name),
                "image_count": int(payload.get("image_count") or 3),
                "use_image_governance_review": payload.get("use_image_governance_review"),
            },
            config,
        )
        _write_submission_files(
            platform=platform,
            article_markdown=platform_article,
            output_dir=packs_dir / platform_name,
        )

        results.append(
            {
                "platform": platform,
                "platform_name": platform_name,
                "article_path": str(article_path),
                "draft_source": draft_source,
                "draft_turn": draft_turn,
                "revise_turn": revise_turn,
                "expansion_turns": expansion_turns,
                "revised_source": revised_source,
                "final_decision": review_report.get("decision"),
                "review_score": review_report.get("total_score"),
                "review_report": review_report,
                "target_chars": target_chars,
                "final_char_count": article_assessment["char_count"],
                "final_paragraph_count": article_assessment["paragraph_count"],
                "final_evidence_signals": article_assessment["evidence_signals"],
                "completeness_requirement_met": article_assessment["is_complete"],
                "completeness_reasons": article_assessment["reasons"],
                "publish_result": publish_result,
                "image_review_report": publish_result.get("image_review_report"),
            }
        )

    summary = {
        "contract_name": "release_cycle_result",
        "contract_version": "v1",
        "run_id": run_id,
        "topic": topic,
        "source_platform": source_platform,
        "output_root": str(output_root),
        "platform_results": results,
        "recommended_next_actions": _release_next_actions(results),
    }
    path = config.sessions_dir / f"{run_id}.release.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _resolve_release_root(payload: dict[str, Any], config: AppConfig, topic: str) -> Path:
    raw = str(payload.get("output_root") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    slug = slug_for_filename(topic, "release-cycle")
    path = config.data_dir / "release-cycles" / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_platforms(raw: Any) -> list[str]:
    items = [str(item).strip().lower() for item in (raw or DEFAULT_RELEASE_PLATFORMS) if str(item).strip()]
    deduped: list[str] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _platform_adaptation_message(platform: str, *, source_platform: str, target_chars: int) -> str:
    platform_name = PLATFORM_NAMES.get(platform, platform)
    if platform == "wechat":
        return (
            f"请基于当前母稿输出可直接发布的公众号版，首屏更快亮判断，保留观点推进和论证密度。"
            f"不要压缩成摘要，观点必须讲完整；通常需要接近 {target_chars} 字的展开密度，但以讲清楚为准。"
        )
    if platform == "zhihu":
        return (
            f"请把当前母稿改成知乎版，问题意识更明确，论证更展开，少一点情绪化句子，多一点解释和边界。"
            f"不要缩写成短帖，观点链路要完整；通常需要接近 {target_chars} 字的展开密度，但不要机械凑字。"
        )
    if platform == "xiaohongshu":
        return (
            f"请把当前母稿改成小红书长文版，开头更抓人，段落更短，标题和小标题更像笔记，但不要写成空泛鸡汤。"
            f"保持长文密度，把观点讲完整；通常需要接近 {target_chars} 字的展开密度。"
        )
    if platform == "renrendoushichanpinjingli":
        return (
            f"请把当前母稿改成人都是产品经理版，强调产品、组织、工作流和方法启发，减少纯情绪判断。"
            f"要把方法和判断说透，通常需要接近 {target_chars} 字的展开密度。"
        )
    if platform == "toutiao":
        return (
            f"请把当前母稿改成今日头条版，开头直接、判断明确、节奏更快，保留信息密度。"
            f"不要只给短观点，至少要把这条判断为什么成立讲清楚；参考展开密度接近 {target_chars} 字。"
        )
    if platform == "csdn":
        return (
            f"请把当前母稿改成 CSDN 版，结构更工程化，增加方法、框架、判断拆解，适合技术和产品读者。"
            f"需要有完整拆解，不要只保留结论；参考展开密度接近 {target_chars} 字。"
        )
    if platform == "juejin":
        return (
            f"请把当前母稿改成掘金版，保留观点，但更像面向互联网从业者的深度经验帖。"
            f"要把经验判断讲透，参考展开密度接近 {target_chars} 字。"
        )
    return (
        f"请把当前{PLATFORM_NAMES.get(source_platform, source_platform)}母稿改成适合{platform_name}发布的版本。"
        f"不要压缩成摘要，观点必须讲完整；参考展开密度接近 {target_chars} 字。"
    )


def _platform_target_chars(platform: str, payload: dict[str, Any]) -> int:
    overrides = dict(payload.get("platform_target_chars") or payload.get("platform_target_char_overrides") or {})
    raw_value = overrides.get(platform, payload.get("target_platform_chars", DEFAULT_PLATFORM_TARGET_CHARS))
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = DEFAULT_PLATFORM_TARGET_CHARS
    return max(500, value)


def _article_char_count(article_markdown: str) -> int:
    paragraphs = split_paragraphs(article_markdown)
    body_parts: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        normalized = paragraph.strip()
        if index == 0 and normalized.startswith("# "):
            continue
        body_parts.append(normalized.lstrip("#").strip())
    sample = "\n".join(body_parts) if body_parts else article_markdown
    cleaned = re.sub(r"[`*_>\-\[\]\(\)!|]", "", sample)
    cleaned = compact_whitespace(cleaned).replace(" ", "")
    return len(cleaned)


def _assess_platform_article(
    *,
    article_markdown: str,
    source_article_markdown: str,
    platform: str,
    target_chars: int,
) -> dict[str, Any]:
    char_count = _article_char_count(article_markdown)
    source_char_count = _article_char_count(source_article_markdown)
    paragraphs = [paragraph for paragraph in split_paragraphs(article_markdown) if not paragraph.startswith("#")]
    paragraph_count = len(paragraphs)
    evidence_signals = count_evidence_signals(article_markdown)
    soft_floor = _platform_soft_floor(platform, source_char_count=source_char_count, target_chars=target_chars)
    min_paragraphs = _platform_min_paragraphs(platform)
    min_evidence_signals = _platform_min_evidence_signals(platform)

    reasons: list[str] = []
    if char_count < soft_floor:
        reasons.append(f"正文展开不足，当前约 {char_count} 字，参考展开密度至少约 {soft_floor} 字")
    if paragraph_count < min_paragraphs:
        reasons.append(f"段落过少，当前只有 {paragraph_count} 段，难以把观点讲完整")
    if evidence_signals < min_evidence_signals:
        reasons.append(f"论证信号偏少，当前只有 {evidence_signals} 处场景/因果/案例支撑")

    return {
        "char_count": char_count,
        "source_char_count": source_char_count,
        "paragraph_count": paragraph_count,
        "evidence_signals": evidence_signals,
        "soft_floor": soft_floor,
        "target_chars": target_chars,
        "min_paragraphs": min_paragraphs,
        "min_evidence_signals": min_evidence_signals,
        "is_complete": not reasons,
        "reasons": reasons,
    }


def _platform_soft_floor(platform: str, *, source_char_count: int, target_chars: int) -> int:
    if platform in {"xiaohongshu", "toutiao"}:
        return min(target_chars, max(1000, int(source_char_count * 0.7)))
    return min(target_chars, max(1200, int(source_char_count * 0.8)))


def _platform_min_paragraphs(platform: str) -> int:
    if platform in {"xiaohongshu", "toutiao"}:
        return 5
    return 6


def _platform_min_evidence_signals(platform: str) -> int:
    if platform == "xiaohongshu":
        return 1
    return 2


def _platform_expansion_message(*, platform: str, assessment: dict[str, Any], round_index: int) -> str:
    platform_name = PLATFORM_NAMES.get(platform, platform)
    round_hint = "这已经是二次扩写，请明显补足内容深度和篇幅。" if round_index > 0 else "请直接扩写成完整长文。"
    reason_text = "；".join(assessment["reasons"]) if assessment["reasons"] else "当前版本仍偏摘要化"
    return (
        f"当前这版{platform_name}还没有把观点讲完整，主要问题是：{reason_text}。"
        f"{round_hint}"
        "请保留原文核心判断和主线结构，补足为什么会这样、具体场景、案例或类比、潜在反例、后果与行动启发。"
        f"参考展开密度约 {assessment['soft_floor']} 到 {assessment['target_chars']} 字，但不要机械凑字，关键是读完后判断完整。"
        "不要写成提纲、修改说明或摘要，不要空话和重复句，直接输出可发布正文全文。"
    )


def _apply_completeness_guard(
    review_report: dict[str, Any],
    *,
    platform: str,
    assessment: dict[str, Any],
) -> dict[str, Any]:
    if assessment["is_complete"]:
        return review_report

    next_report = dict(review_report)
    next_report["decision"] = "revise" if review_report.get("decision") == "pass" else review_report.get("decision")
    top_issues = list(review_report.get("top_issues") or [])
    rewrite_actions = list(review_report.get("rewrite_actions") or [])
    top_issues.insert(
        0,
        {
            "issue_type": "platform_completeness",
            "severity": "high",
            "summary": f"{PLATFORM_NAMES.get(platform, platform)}平台稿观点展开不足",
            "evidence": "；".join(assessment["reasons"]),
        },
    )
    rewrite_actions.insert(0, "继续扩写成完整观点稿，补足论证、场景、案例、边界和结论展开。")
    next_report["top_issues"] = top_issues[:5]
    next_report["rewrite_actions"] = list(dict.fromkeys(rewrite_actions))
    return next_report


def _write_submission_files(*, platform: str, article_markdown: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    title = _extract_title(article_markdown)
    summary = _article_summary(article_markdown)
    intro = _opening_intro(article_markdown)
    tags = _recommended_tags(platform, article_markdown)

    strategy = "\n".join(
        [
            "# 投递策略",
            "",
            "## 标题 A/B/C",
            "",
            f"- A：{title}",
            f"- B：{_alt_title(title, style='question')}",
            f"- C：{_alt_title(title, style='conflict')}",
            "",
            "## 推荐标签",
            "",
            *[f"- `{tag}`" for tag in tags],
            "",
            "## 推荐发布时间",
            "",
            "- 工作日晚间 20:00-22:00",
            "- 周日上午 10:00-11:30",
            "",
            "## 开头导语",
            "",
            intro,
            "",
            "## 结尾 CTA",
            "",
            _closing_cta(platform),
        ]
    )
    template = "\n".join(
        [
            "# 投稿问题-填写模板",
            "",
            "## 文章摘要",
            "",
            summary,
            "",
            "## 适合谁看",
            "",
            *[f"- {item}" for item in _audience_bullets(platform)],
            "",
            "## 读者能得到什么",
            "",
            *[f"- {item}" for item in _reader_value_bullets(platform)],
        ]
    )
    (output_dir / "投递策略.md").write_text(strategy + "\n", encoding="utf-8")
    (output_dir / "投稿问题-填写模板.md").write_text(template + "\n", encoding="utf-8")


def _extract_title(article_markdown: str) -> str:
    for paragraph in split_paragraphs(article_markdown):
        if paragraph.startswith("# "):
            return paragraph[2:].strip()
        stripped = compact_whitespace(paragraph)
        if stripped:
            return stripped[:80]
    return "未命名文章"


def _article_summary(article_markdown: str) -> str:
    paragraphs = [compact_whitespace(item) for item in split_paragraphs(article_markdown) if not item.startswith("#")]
    if not paragraphs:
        return "围绕当前主题输出了一版可发布文章。"
    return " ".join(paragraphs[:2])[:220]


def _opening_intro(article_markdown: str) -> str:
    paragraphs = [compact_whitespace(item) for item in split_paragraphs(article_markdown) if not item.startswith("#")]
    return paragraphs[0] if paragraphs else "这篇文章试图把一个正在发生但还没被充分说清的变化讲清楚。"


def _alt_title(title: str, *, style: str) -> str:
    if style == "question":
        return f"{title}，为什么现在更值得警惕？"
    return f"{title}：真正危险的不是表面看到的那一层"


def _recommended_tags(platform: str, article_markdown: str) -> list[str]:
    tags = ["AI", "行业观察"]
    lower = article_markdown.lower()
    if "产品" in article_markdown:
        tags.append("产品经理")
    if "组织" in article_markdown or "公司" in article_markdown:
        tags.append("组织变化")
    if "模型" in article_markdown:
        tags.append("模型能力")
    if platform in {"csdn", "juejin"}:
        tags.append("技术趋势")
    return tags[:5]


def _closing_cta(platform: str) -> str:
    if platform == "xiaohongshu":
        return "你更担心自己跟不上工具，还是更担心自己只剩工具？评论区聊聊。"
    return "你怎么看这条变化？欢迎留言说说你的判断和反例。"


def _audience_bullets(platform: str) -> list[str]:
    common = ["产品、运营、开发和创始人", "关心 AI 行业变化的人"]
    if platform == "xiaohongshu":
        return ["年轻职场人", "正在学习 AI 工具的人", *common]
    return common


def _reader_value_bullets(platform: str) -> list[str]:
    values = ["获得一条更清晰的判断", "理解平台或行业变化背后的结构性原因"]
    if platform in {"csdn", "juejin"}:
        values.append("拿到更可讨论的方法和框架")
    return values


def _release_next_actions(results: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    not_passed = [item["platform_name"] for item in results if item.get("final_decision") != "pass"]
    if not_passed:
        actions.append(f"这些平台稿还没通过 reviewer：{', '.join(not_passed)}。")
    actions.append("为每个平台补齐公开渠道真实图片后，再刷新图文 DOCX。")
    actions.append("全部平台核完后，再开始今天的新文章。")
    return actions
