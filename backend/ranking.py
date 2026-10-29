import re

from pydantic import BaseModel, ConfigDict, Field

WEIGHTS = {"decision_impact": 40, "evidence_quality": 30, "novelty": 15, "actionability": 15}
SCALE_MAX = 4
MINIMUM = 2


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int = Field(ge=0, le=SCALE_MAX, strict=True)
    reason: str = Field(min_length=10, max_length=1500)


class Ranking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_impact: Assessment
    evidence_quality: Assessment
    novelty: Assessment
    actionability: Assessment

    @property
    def score(self):
        return sum(getattr(self, key).score * weight for key, weight in WEIGHTS.items()) / SCALE_MAX

    @property
    def publishable(self):
        return all(getattr(self, key).score >= MINIMUM for key in WEIGHTS)


def duplicate_key(quote, question):
    def normalize(value):
        return " ".join(re.findall(r"\w+", value.casefold()))

    return normalize(quote), normalize(question)


def sort_key(card):
    ranking = card["ranking"]
    return (
        ranking is None,
        -(card["score"] or 0),
        -ranking["decision_impact"]["score"] if ranking else 0,
        -ranking["evidence_quality"]["score"] if ranking else 0,
        card["created_at"],
        card["id"],
    )
