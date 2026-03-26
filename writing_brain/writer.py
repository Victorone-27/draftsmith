from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .config import AppConfig, ensure_runtime_dirs
from .context_pack import build_context_pack
from .llm import call_packy_chat, call_ppchat_chat
from .text import now_run_id, split_paragraphs, starts_with_heading


DEFAULT_WRITER_SCRIPT = Path.home() / ".codex" / "skills" / "humanizer-zh" / "scripts" / "packy_gemini_chat.py"
DEFAULT_DRAFT_MODEL = "gemini-3.1-pro-preview"
DEFAULT_DRAFT_MAX_TOKENS = 6000
DEFAULT_GEMINI_REVISE_MODEL = "gemini-3.1-pro-preview"
DEFAULT_GEMINI_REVISE_MAX_TOKENS = 6000
DEFAULT_CLAUDE_REVISE_MODEL = "claude-opus-4-6"
DEFAULT_GPT_REVISE_MODEL = "gpt-5.4"
DEFAULT_PPCHAT_REVISE_MAX_TOKENS = 6000
DEFAULT_GEMINI_REVISE_ATTEMPTS = 2


def run_writer_turn(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    ensure_runtime_dirs(config)
    run_id = str(payload.get("run_id") or now_run_id())
    task_mode = str(payload.get("task_mode") or "brainstorm").strip().lower()
    user_message = str(payload.get("user_message") or payload.get("message") or "").strip()
    context_pack = dict(payload.get("context_pack") or {})
    if not context_pack:
        context_pack = build_context_pack(payload, config)

    current_draft = str(payload.get("current_draft") or payload.get("draft_text") or "").strip()
    prompt = _build_writer_prompt(
        task_mode=task_mode,
        user_message=user_message,
        context_pack=context_pack,
        current_draft=current_draft,
        review_report=dict(payload.get("review_report") or {}),
    )
    revision_owner = str(payload.get("revision_owner") or "gemini").strip().lower()
    # Brainstorm is intended to stay on the current GPT-side conversation layer.
    # Draft/revise are the only stages that should hit the downstream Gemini route.
    force_prompt_only = bool(payload.get("force_prompt_only", False)) or task_mode == "brainstorm"
    model_result = _call_writer_model(
        prompt,
        task_mode=task_mode,
        force_prompt_only=force_prompt_only,
        revision_owner=revision_owner,
        current_draft=current_draft,
    )

    result = {
        "contract_name": "writer_response",
        "contract_version": "v1",
        "run_id": run_id,
        "task_mode": task_mode,
        "revision_owner": revision_owner if task_mode == "revise" else "",
        "mode": model_result["mode"],
        "provider": model_result.get("provider", ""),
        "model": model_result.get("model", ""),
        "writer_prompt": prompt,
        "reply_text": model_result["reply_text"],
        "context_pack": context_pack,
        "recommended_next_actions": _recommended_next_actions(task_mode, model_result["mode"], str(model_result.get("provider") or "")),
    }
    _persist_writer_turn(config, run_id, result)
    return result


def _build_writer_prompt(
    *,
    task_mode: str,
    user_message: str,
    context_pack: dict[str, Any],
    current_draft: str,
    review_report: dict[str, Any],
) -> str:
    claims = "\n".join(
        f"- {item.get('title')}: {item.get('summary')}"
        for item in context_pack.get("core_claims", [])[:4]
    ) or "- 暂无可用 claim"
    structures = "\n".join(
        f"- {item.get('name')}: {item.get('reason')}"
        for item in context_pack.get("preferred_structures", [])[:2]
    ) or "- 暂无明确结构建议"
    format_rules = "\n".join(
        [
            "- 按公众号母稿排版输出，不要写“标题：”“导语：”“正文：”“标题备选”这类标签。",
            "- 第一行只写一个 Markdown H1 标题，格式类似 `# 文章标题`。",
            "- 标题后直接进入正文，不要加 `---` 分割线，不要单独写导语标签。",
            "- 默认整篇只用自然段推进，不要写 `**1.**`、`**2.**` 这种硬编号小标题，也尽量不要写 `##` 小标题。",
            "- 只有在信息密度明显过高、自然分段无法承载时，才允许 1 到 2 个 `##` 小标题。",
            "- 段落保持简短，尽量贴近作者现有母稿的排版节奏。",
        ]
    )
    length_rules = "\n".join(
        [
            "- 如果是公众号观点母稿，默认写到能把判断、论证和边界讲透，不要为了克制而过度收短。",
            "- 在用户没有明确要求短文时，公众号观点母稿通常不少于 1400 字符。",
            "- 必须覆盖点没有讲透前，不要提前收尾。",
        ]
    )
    knowledge = "\n".join(
        f"- [{item.get('entity_type')}] {item.get('statement')}"
        for item in context_pack.get("memory_knowledge", [])[:6]
    ) or "- 暂无长期记忆知识体"
    must_cover = "\n".join(f"- {item}" for item in context_pack.get("must_cover_points", [])[:6]) or "- 暂无"
    style_rules = "\n".join(f"- {item}" for item in context_pack.get("style_rules", [])[:6]) or "- 暂无"
    platform_rules = "\n".join(f"- {item}" for item in context_pack.get("platform_rules", [])[:6]) or "- 暂无"
    project_constraints = "\n".join(f"- {item}" for item in context_pack.get("project_constraints", [])[:6]) or "- 暂无"
    evidence_needs = "\n".join(f"- {item}" for item in context_pack.get("evidence_needs", [])[:6]) or "- 暂无"
    mode_instruction = _task_mode_instruction(task_mode)
    review_issues = "\n".join(
        f"- {item.get('summary')}"
        for item in review_report.get("top_issues", [])[:5]
        if item.get("summary")
    ) or "- 暂无 reviewer 问题"
    rewrite_actions = "\n".join(f"- {item}" for item in review_report.get("rewrite_actions", [])[:6]) or "- 暂无 reviewer 改写要求"

    return f"""你是作者的长期写作搭档。

当前任务模式：{task_mode}
{mode_instruction}

主题：{context_pack.get("topic", "")}
平台：{context_pack.get("platform", "")}
受众：{context_pack.get("audience", "")}
目标：{context_pack.get("user_goal", "")}
文风：{context_pack.get("tone_target", "")}

作者当前补充：
{user_message or "无"}

必须覆盖：
{must_cover}

历史观点参考（仅在与当前主题直接相关时才可复用）：
{claims}

推荐结构：
{structures}

排版规则：
{format_rules}

长度规则：
{length_rules}

长期记忆知识体（只在直接相关时使用）：
{knowledge}

风格规则：
{style_rules}

平台规则：
{platform_rules}

项目硬约束：
{project_constraints}

待补论据：
{evidence_needs}

当前草稿：
{current_draft or "无"}

reviewer 问题：
{review_issues}

reviewer 修改要求：
{rewrite_actions}

要求：
1. 历史观点和长期记忆只能作为辅助参考；如果和当前主题不直接相关，就忽略，不要硬塞进正文。
2. 不要写空泛正确废话。
3. 需要区分判断、论据、结构建议。
4. 如果是 brainstorm，就先帮作者收束思路。
5. 如果是 draft，就直接给可用初稿。
6. 如果是 revise，就按已有方向改稿，不要重起炉灶。
7. 如果存在当前草稿和 reviewer 问题，优先按 reviewer 要求定向修正。
"""


def _task_mode_instruction(task_mode: str) -> str:
    if task_mode == "draft":
        return "请直接给出可用初稿，优先成稿，不要长篇解释。"
    if task_mode == "revise":
        return "请基于已有方向修稿，重点处理问题，不要整体推翻。"
    return "请先和作者一起收束思路，必要时给 brief、结构和开头方向。"


def _call_writer_model(
    prompt: str,
    *,
    task_mode: str,
    force_prompt_only: bool,
    revision_owner: str = "gemini",
    current_draft: str = "",
) -> dict[str, str]:
    if force_prompt_only:
        return {
            "mode": "prompt_only",
            "reply_text": "模型未调用。请使用 writer_prompt 继续对话，或配置 PACKYAPI_API_KEY 后重试。",
            "provider": "prompt_only",
            "model": "",
        }
    if task_mode == "revise":
        if revision_owner.strip().lower() == "gemini":
            return _call_gemini_revise_with_retry(prompt, current_draft=current_draft)
        revise_result = _call_revise_model(prompt, revision_owner=revision_owner)
        if revise_result["mode"] == "model_output":
            return _validate_revise_output(revise_result, revision_owner=revision_owner, current_draft=current_draft)
        return revise_result

    api_key = os.environ.get("PACKYAPI_API_KEY") or os.environ.get("WRITING_BRAIN_API_KEY")
    if api_key:
        return _call_packy_api(prompt, api_key)

    # Compatibility fallback for an existing local helper script.
    script = Path(os.environ.get("WRITING_BRAIN_WRITER_SCRIPT", str(DEFAULT_WRITER_SCRIPT))).expanduser()
    if not script.exists():
        return {
            "mode": "prompt_only",
            "reply_text": "模型未调用。请使用 writer_prompt 继续对话，或配置 PACKYAPI_API_KEY 后重试。",
            "provider": "prompt_only",
            "model": "",
        }
    import subprocess
    import sys

    command = [sys.executable, str(script), prompt]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=180)
    except Exception as exc:
        return {
            "mode": "prompt_only",
            "reply_text": f"模型调用失败，已退回 prompt_only。错误：{exc!r}",
            "provider": "local_script",
            "model": script.name,
        }
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit={completed.returncode}"
        return {
            "mode": "prompt_only",
            "reply_text": f"模型调用失败，已退回 prompt_only。错误：{detail}",
            "provider": "local_script",
            "model": script.name,
        }
    return {
        "mode": "model_output",
        "reply_text": completed.stdout.strip(),
        "provider": "local_script",
        "model": script.name,
    }


def _call_packy_api(prompt: str, api_key: str) -> dict[str, str]:
    _ = api_key
    max_tokens = _resolve_draft_max_tokens()
    result = call_packy_chat(
        prompt=prompt,
        system_prompt="你是中文写作搭档。回答自然、克制、少模板腔，优先帮助作者把判断讲清楚。",
        default_model=DEFAULT_DRAFT_MODEL,
        model_env_vars=["PACKYAPI_MODEL", "WRITING_BRAIN_WRITER_MODEL"],
        temperature=0.3,
        max_tokens=max_tokens,
    )
    return {
        "mode": str(result.get("mode") or "prompt_only"),
        "reply_text": _normalize_article_output_text(str(result.get("reply_text") or "")),
        "provider": str(result.get("provider") or "packyapi"),
        "model": str(result.get("model") or DEFAULT_DRAFT_MODEL),
    }


def _call_gemini_revise_model(prompt: str) -> dict[str, str]:
    api_key = os.environ.get("PACKYAPI_API_KEY") or os.environ.get("WRITING_BRAIN_API_KEY")
    if api_key:
        max_tokens = _resolve_gemini_revise_max_tokens()
        result = call_packy_chat(
            prompt=prompt,
            system_prompt="你是中文改稿搭档。你要严格按照 reviewer 问题定向修稿，不要重起炉灶，不要偷懒。",
            default_model=DEFAULT_GEMINI_REVISE_MODEL,
            model_env_vars=["PACKYAPI_REVISE_MODEL", "WRITING_BRAIN_GEMINI_REVISE_MODEL", "PACKYAPI_MODEL", "WRITING_BRAIN_WRITER_MODEL"],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return {
            "mode": str(result.get("mode") or "prompt_only"),
            "reply_text": _normalize_article_output_text(str(result.get("reply_text") or "")),
            "provider": str(result.get("provider") or "packyapi"),
            "model": str(result.get("model") or DEFAULT_GEMINI_REVISE_MODEL),
        }

    script = Path(os.environ.get("WRITING_BRAIN_WRITER_SCRIPT", str(DEFAULT_WRITER_SCRIPT))).expanduser()
    if not script.exists():
        return {
            "mode": "prompt_only",
            "reply_text": "Gemini revise 未调用。请配置 PACKYAPI_API_KEY 后重试。",
            "provider": "prompt_only",
            "model": "",
        }
    import sys

    command = [sys.executable, str(script), prompt]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=180)
    except Exception as exc:
        return {
            "mode": "prompt_only",
            "reply_text": f"Gemini revise 调用失败，已退回 prompt_only。错误：{exc!r}",
            "provider": "local_script",
            "model": script.name,
        }
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit={completed.returncode}"
        return {
            "mode": "prompt_only",
            "reply_text": f"Gemini revise 调用失败，已退回 prompt_only。错误：{detail}",
            "provider": "local_script",
            "model": script.name,
        }
    return {
        "mode": "model_output",
        "reply_text": completed.stdout.strip(),
        "provider": "local_script",
        "model": script.name,
    }


def _call_claude_revise_model(prompt: str) -> dict[str, str]:
    result = call_ppchat_chat(
        prompt=prompt,
        system_prompt="你是中文改稿搭档。你要严格按照 reviewer 问题定向修稿，不要重起炉灶，不要偷懒。",
        default_model=DEFAULT_CLAUDE_REVISE_MODEL,
        model_env_vars=["PPCHAT_REVISE_MODEL", "WRITING_BRAIN_CLAUDE_REVISE_MODEL", "WRITING_BRAIN_REVISE_MODEL", "OPENAI_MODEL", "ANTHROPIC_MODEL"],
        temperature=0.2,
        max_tokens=DEFAULT_PPCHAT_REVISE_MAX_TOKENS,
    )
    return {
        "mode": str(result.get("mode") or "prompt_only"),
        "reply_text": _normalize_article_output_text(str(result.get("reply_text") or "")),
        "provider": str(result.get("provider") or "ppchat"),
        "model": str(result.get("model") or DEFAULT_CLAUDE_REVISE_MODEL),
    }


def _call_gpt_revise_model(prompt: str) -> dict[str, str]:
    result = call_ppchat_chat(
        prompt=prompt,
        system_prompt="你是中文改稿搭档。你要严格按照 reviewer 问题定向修稿，不要重起炉灶，不要偷懒。",
        default_model=DEFAULT_GPT_REVISE_MODEL,
        model_env_vars=[
            "PPCHAT_GPT_REVISE_MODEL",
            "WRITING_BRAIN_GPT_REVISE_MODEL",
            "PPCHAT_PROCEDURAL_MODEL",
            "WRITING_BRAIN_PROCEDURAL_MODEL",
        ],
        temperature=0.2,
        max_tokens=DEFAULT_PPCHAT_REVISE_MAX_TOKENS,
    )
    return {
        "mode": str(result.get("mode") or "prompt_only"),
        "reply_text": _normalize_article_output_text(str(result.get("reply_text") or "")),
        "provider": str(result.get("provider") or "ppchat"),
        "model": str(result.get("model") or DEFAULT_GPT_REVISE_MODEL),
    }


def _call_revise_model(prompt: str, *, revision_owner: str) -> dict[str, str]:
    owner = revision_owner.strip().lower() or "gemini"
    if owner == "claude":
        return _call_claude_revise_model(prompt)
    if owner in {"gpt", "gpt5_4", "gpt-5.4"}:
        return _call_gpt_revise_model(prompt)
    return _call_gemini_revise_model(prompt)


def _call_gemini_revise_with_retry(prompt: str, *, current_draft: str) -> dict[str, str]:
    last_result: dict[str, str] | None = None
    for _ in range(DEFAULT_GEMINI_REVISE_ATTEMPTS):
        candidate = _call_gemini_revise_model(prompt)
        last_result = candidate
        if candidate["mode"] != "model_output":
            continue
        validated = _validate_revise_output(candidate, revision_owner="gemini", current_draft=current_draft)
        if validated["mode"] == "model_output":
            return validated
        last_result = validated
    return last_result or {
        "mode": "prompt_only",
        "reply_text": "Gemini revise 未调用。请配置 PACKYAPI_API_KEY 后重试。",
        "provider": "prompt_only",
        "model": "",
    }


def _resolve_gemini_revise_max_tokens() -> int:
    raw = (
        os.environ.get("WRITING_BRAIN_GEMINI_REVISE_MAX_TOKENS")
        or os.environ.get("PACKYAPI_REVISE_MAX_TOKENS")
        or ""
    ).strip()
    if raw:
        try:
            value = int(raw)
            if value >= 512:
                return value
        except ValueError:
            pass
    return DEFAULT_GEMINI_REVISE_MAX_TOKENS


def _resolve_draft_max_tokens() -> int:
    raw = (
        os.environ.get("WRITING_BRAIN_DRAFT_MAX_TOKENS")
        or os.environ.get("PACKYAPI_DRAFT_MAX_TOKENS")
        or ""
    ).strip()
    if raw:
        try:
            value = int(raw)
            if value >= 512:
                return value
        except ValueError:
            pass
    return DEFAULT_DRAFT_MAX_TOKENS


def _validate_revise_output(result: dict[str, str], *, revision_owner: str, current_draft: str) -> dict[str, str]:
    text = _normalize_article_output_text(str(result.get("reply_text") or ""))
    owner = revision_owner.strip().lower() or "gemini"
    if owner != "gemini":
        return {**result, "reply_text": text}
    if _looks_like_complete_article(text, baseline_length=len(current_draft)):
        return {**result, "reply_text": text}

    return {
        "mode": "prompt_only",
        "reply_text": "Gemini revise 返回了不完整的结果，已判定为失败。常见原因：只输出修改说明、正文被截断、段落数不足。",
        "provider": str(result.get("provider") or "packyapi"),
        "model": str(result.get("model") or DEFAULT_GEMINI_REVISE_MODEL),
    }


def _normalize_article_output_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""

    markers = [
        "**修改后正文：**",
        "修改后正文：",
        "修改后正文:",
        "以下是修改后正文：",
        "以下是修改后正文:",
        "正文如下：",
        "正文如下:",
    ]
    for marker in markers:
        if marker in text:
            _, remainder = text.split(marker, 1)
            candidate = remainder.strip()
            if candidate:
                text = candidate
                break
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return text

    stripped: list[str] = []
    skipping_meta = True
    meta_prefixes = (
        "这里是",
        "以下是",
        "本次修改",
        "修改重点",
        "根据 reviewer",
        "根据reviewer",
        "按 reviewer",
        "按reviewer",
        "我根据",
    )
    metadata_headings = {"## 正文", "正文", "## 标题备选", "标题备选"}
    for paragraph in paragraphs:
        normalized = paragraph.strip()
        compact = normalized.replace(" ", "")
        normalized = _normalize_heading_paragraph(normalized)
        if not normalized:
            continue
        compact = normalized.replace(" ", "")
        if skipping_meta:
            if compact in {"***", "---", "———"}:
                continue
            if any(compact.startswith(prefix) for prefix in meta_prefixes):
                continue
            if normalized in metadata_headings:
                continue
            skipping_meta = False
        if normalized in metadata_headings or normalized in {"**导语：**", "导语：", "导语:", "## 导语"}:
            continue
        stripped.append(normalized)
    if stripped:
        return "\n\n".join(stripped).strip()
    return text


def _normalize_heading_paragraph(paragraph: str) -> str:
    text = paragraph.strip()
    if not text:
        return ""

    title_patterns = [
        r"^\*\*标题[:：]\*\*\s*(.+?)\s*\*\*$",
        r"^\*\*标题[:：]\s*(.+?)\*\*$",
        r"^#\s*标题[:：]\s*(.+)$",
        r"^标题[:：]\s*(.+)$",
    ]
    for pattern in title_patterns:
        match = re.match(pattern, text)
        if match:
            title = match.group(1).strip().strip("*").strip()
            if title:
                return f"# {title}"

    intro_patterns = [
        r"^\*\*导语[:：]\*\*\s*(.+)$",
        r"^\*\*导语[:：]\s*(.+)\*\*$",
        r"^导语[:：]\s*(.+)$",
    ]
    for pattern in intro_patterns:
        match = re.match(pattern, text)
        if match:
            return match.group(1).strip().strip("*").strip()

    return text


def _looks_like_complete_article(text: str, *, baseline_length: int) -> bool:
    if not text:
        return False
    if "重点动作" in text or "修改说明" in text or "这里是按照" in text:
        return False
    paragraphs = split_paragraphs(text)
    if len(paragraphs) < 5:
        return False
    has_heading = starts_with_heading(text)
    first_paragraph = paragraphs[0] if paragraphs else ""
    if not has_heading and (len(first_paragraph) < 12 or "。" not in first_paragraph):
        return False
    if len(text) < 180:
        return False
    if text.count("。") < 6:
        return False
    if baseline_length >= 600 and len(text) < int(baseline_length * 0.55):
        return False
    if text[-1] not in "。！？!?”」』":
        return False
    return True


def _recommended_next_actions(task_mode: str, mode: str, provider: str) -> list[str]:
    actions: list[str] = []
    if mode == "prompt_only":
        actions.append("先检查 writer_prompt 是否覆盖了你真正想表达的核心判断。")
        if task_mode == "brainstorm":
            actions.append("当前模式默认不调用 Gemini，请直接基于 reply_text 和 writer_prompt 继续对话。")
        elif task_mode == "revise" and provider in {"packyapi", "local_script", "prompt_only"}:
            actions.append("当前 revise 默认走 Gemini 通道。请检查 PACKYAPI key 或本地 Gemini 脚本是否可用。")
        else:
            actions.append("配置 Gemini 通道后可直接重跑 writer-chat。")
    if task_mode == "brainstorm":
        actions.append("确认题目、核心判断和结构后，再进入 draft。")
    elif task_mode == "draft":
        actions.append("初稿完成后，直接送 reviewer 打分。")
    else:
        actions.append("修稿后重新走 reviewer，确认问题是否真正解决。")
    return actions


def _persist_writer_turn(config: AppConfig, run_id: str, payload: dict[str, Any]) -> None:
    path = config.sessions_dir / f"{run_id}.writer.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
