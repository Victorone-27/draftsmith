from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_DATA_DIR = Path.home() / "Documents" / "文稿" / "写作系统"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path

    @property
    def claims_dir(self) -> Path:
        return self.data_dir / "claims"

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"

    @property
    def structures_dir(self) -> Path:
        return self.data_dir / "structures"

    @property
    def image_plans_dir(self) -> Path:
        return self.data_dir / "image-plans"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def feedback_history_dir(self) -> Path:
        return self.data_dir / "feedback-history"

    @property
    def knowledge_entities_dir(self) -> Path:
        return self.data_dir / "knowledge-entities"

    @property
    def review_history_dir(self) -> Path:
        return self.data_dir / "review-history"

    @property
    def articles_dir(self) -> Path:
        return self.data_dir / "articles"

    @property
    def daily_digests_dir(self) -> Path:
        return self.data_dir / "daily-digests"

    @property
    def hotspots_dir(self) -> Path:
        return self.data_dir / "hotspots"

    @property
    def published_dir(self) -> Path:
        return self.data_dir.parent

    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def preferences_dir(self) -> Path:
        return self.data_dir / "preferences"

    @property
    def usage_dir(self) -> Path:
        return self.data_dir / "usage"


def load_config(data_dir: Optional[str] = None) -> AppConfig:
    raw = data_dir or os.environ.get("WRITING_BRAIN_DATA_DIR")
    resolved = Path(raw).expanduser() if raw else DEFAULT_DATA_DIR
    return AppConfig(data_dir=resolved)


def ensure_runtime_dirs(config: AppConfig) -> None:
    for path in [
        config.data_dir,
        config.sessions_dir,
        config.feedback_history_dir,
        config.knowledge_entities_dir,
        config.review_history_dir,
        config.articles_dir,
        config.daily_digests_dir,
        config.hotspots_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)
