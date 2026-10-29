import json
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("ADR_DATA_DIR", ROOT / ".data")).resolve()
REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class Settings(BaseModel):
    slack_workspace: str = ""
    research_repositories: list[str] = Field(default_factory=list)

    @field_validator("slack_workspace")
    @classmethod
    def workspace_domain(cls, value):
        if value and not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.slack\.com", value):
            raise ValueError("Use a Slack workspace hostname, such as example.slack.com.")
        return value

    @field_validator("research_repositories")
    @classmethod
    def repository_names(cls, values):
        if any(not REPO_PATTERN.fullmatch(repo) for repo in values):
            raise ValueError("Research repositories must use owner/repository names.")
        return values


def load_settings():
    path = DATA / "config.json"
    values = json.loads(path.read_text()) if path.exists() else {}
    if "ADR_SLACK_WORKSPACE" in os.environ:
        values["slack_workspace"] = os.environ["ADR_SLACK_WORKSPACE"]
    if "ADR_RESEARCH_REPOS" in os.environ:
        values["research_repositories"] = [
            repo.strip() for repo in os.environ["ADR_RESEARCH_REPOS"].split(",") if repo.strip()
        ]
    return Settings.model_validate(values)


settings = load_settings()
