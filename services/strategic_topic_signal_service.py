from __future__ import annotations

"""
strategic_topic_signal_service.py

Shared deterministic topic/action signal extraction for AI Event Intelligence.

V1.1.2 verified baseline. It includes the V1.1 fixes and a unique version marker to prevent stale-file confusion.

V1.1 fixes two problems found in V1.0:

1. Broad-AI TOPIC detection no longer treats an arbitrary standalone ``ai``
   token inside a longer topic as sufficient.  Broad-AI topic routing is based
   on explicit AI-topic phrases, with one deliberate exception: the entire
   topic may be exactly ``AI``.

2. Constraint/consequence relation patterns cannot manufacture AI relevance
   from generic words such as ``model``.  Relation evidence must itself contain
   an explicit AI anchor.  The relevance and linkage policies then consume the
   same evidence bundle.

This remains a deterministic BASELINE.  It is not the final whole-document
semantic relevance solution.

No network, LLM, database, or learned-model dependency is used.
"""

from dataclasses import asdict, dataclass
import re
from typing import Any, Sequence


SERVICE_VERSION = (
    "strategic-topic-signal-service-"
    "v1.1.3-shared-evidence-baseline-regex-precision-verified"
)


STRENGTH_SCORE = {
    "weak": 1.0,
    "medium": 2.0,
    "strong": 3.0,
}


@dataclass(frozen=True)
class SignalMatch:
    signal_id: str
    signal_family: str
    strength: str
    span: str
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TopicSignalBundle:
    topic: str
    broad_ai_topic: bool
    text: str
    ai_signals: tuple[SignalMatch, ...]
    material_action_signals: tuple[SignalMatch, ...]
    robotics_signals: tuple[SignalMatch, ...]
    ai_infrastructure_signals: tuple[SignalMatch, ...]
    generic_infrastructure_signals: tuple[SignalMatch, ...]
    constraint_relation_signals: tuple[SignalMatch, ...]
    consequence_relation_signals: tuple[SignalMatch, ...]
    generic_topic_signals: tuple[SignalMatch, ...]

    @property
    def has_material_action(self) -> bool:
        return bool(self.material_action_signals)

    @property
    def max_ai_strength_score(self) -> float:
        if not self.ai_signals:
            return 0.0
        return max(
            STRENGTH_SCORE.get(item.strength, 0.0)
            for item in self.ai_signals
        )

    @property
    def has_explicit_ai_evidence(self) -> bool:
        """True only when shared AI evidence exists in the assessed text."""
        return bool(self.ai_signals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_version": SERVICE_VERSION,
            "topic": self.topic,
            "broad_ai_topic": self.broad_ai_topic,
            "ai_signals": [item.to_dict() for item in self.ai_signals],
            "material_action_signals": [
                item.to_dict() for item in self.material_action_signals
            ],
            "robotics_signals": [item.to_dict() for item in self.robotics_signals],
            "ai_infrastructure_signals": [
                item.to_dict() for item in self.ai_infrastructure_signals
            ],
            "generic_infrastructure_signals": [
                item.to_dict() for item in self.generic_infrastructure_signals
            ],
            "constraint_relation_signals": [
                item.to_dict() for item in self.constraint_relation_signals
            ],
            "consequence_relation_signals": [
                item.to_dict() for item in self.consequence_relation_signals
            ],
            "generic_topic_signals": [
                item.to_dict() for item in self.generic_topic_signals
            ],
        }


class StrategicTopicSignalService:
    """Extract shared deterministic evidence used by topic-admission policies."""

    # IMPORTANT: this is topic-routing vocabulary, not article/event text
    # vocabulary.  Do not add a generic standalone ``ai`` alternative here.
    # Exact topic ``AI`` is handled explicitly by is_broad_ai_topic().
    _BROAD_AI_TOPIC_PHRASE_RE = re.compile(
        r"\b(?:"
        r"artificial\s+intelligence|"
        r"generative\s+ai|"
        r"genai|"
        r"agentic\s+ai|"
        r"physical\s+ai|"
        r"machine\s+learning|"
        r"deep\s+learning|"
        r"large\s+language\s+models?|"
        r"foundation\s+models?|"
        r"ai\s+(?:"
        r"news|developments?|industry|market|systems?|models?|agents?|"
        r"technology|technologies|infrastructure|compute|chips?|regulation|"
        r"policy|governance|safety|adoption|applications?|drug\s+discovery"
        r")"
        r")\b",
        re.I,
    )

    # Shared AI evidence for EVENT/SOURCE TEXT.  Bare ``AI`` belongs here.
    _AI_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
        (
            "explicit_ai_language",
            "strong",
            re.compile(
                r"(?:"
                r"\bartificial\s+intelligence\b|"
                r"\bgenerative\s+ai\b|"
                r"\bgenai\b|"
                r"\bagentic\s+ai\b|"
                r"\bphysical\s+ai\b|"
                r"\blarge\s+language\s+models?\b|"
                r"\bllms?\b|"
                r"\bfoundation\s+models?\b|"
                r"\bmachine\s+learning\b|"
                r"\bdeep\s+learning\b|"
                r"\bai\s+agents?\b|"
                r"\bai\s+models?\b|"
                r"\bai[- ](?:driven|enabled|powered|native|based)\b|"
                r"\bai\b"
                r")",
                re.I,
            ),
        ),
        (
            "named_ai_provider_or_product",
            "medium",
            re.compile(
                r"\b(?:"
                r"anthropic|openai|claude|chatgpt|copilot|gemini|"
                r"gpt(?:-[0-9a-z.]+)?"
                r")\b",
                re.I,
            ),
        ),
        (
            "ai_protocol_or_model_ecosystem",
            "medium",
            re.compile(
                r"\b(?:"
                r"model\s+context\s+protocol|mcp\b|model\s+provider|"
                r"model\s+routing|foundation\s+model\s+provider"
                r")\b",
                re.I,
            ),
        ),
    )

    _MATERIAL_ACTION_RE = re.compile(
        r"\b(?:"
        r"announc(?:e|ed|es|ing)|launch(?:ed|es|ing)?|releas(?:e|ed|es|ing)|"
        r"deploy(?:ed|s|ing|ment)?|adopt(?:ed|s|ion|ing)?|"
        r"partner(?:ed|s|ship|ing)?|collaborat(?:e|ed|es|ing|ion)|"
        r"sign(?:ed|s|ing)?|contract(?:ed|s|ing)?|agreement|deal|"
        r"invest(?:ed|s|ment|ing)?|fund(?:ed|s|ing)?|rais(?:e|ed|es|ing)|"
        r"acquir(?:e|ed|es|ing)|acquisitions?|merg(?:e|ed|es|ing|er)|"
        r"build(?:s|ing|out|built)?|open(?:ed|s|ing)?|expand(?:ed|s|ing)?|"
        r"approve(?:d|s|al)?|ban(?:ned|s|ning)?|"
        r"restrict(?:ed|s|ing|ion)?|regulat(?:e|ed|es|ing|ion)|"
        r"sue(?:d|s|ing)?|rule(?:d|s|ing)?|add(?:ed|s|ing)?|"
        r"cut(?:s|ting)?|increase(?:d|s|ing)?|decrease(?:d|s|ing)?|"
        r"grew|growth|reported|reached|won|lost|selected|awarded|"
        r"introduced|unveiled|halt(?:ed|s|ing)?|cancel(?:led|ed|s|ing)?|"
        r"delay(?:ed|s|ing)?|license(?:d|s|ing)?|"
        r"commerciali[sz](?:e|ed|es|ing|ation)|dos(?:e|ed|es|ing)|"
        r"completed|entered"
        r")\b",
        re.I,
    )

    _ROBOTICS_RE = re.compile(
        r"\b(?:robots?|robotics|humanoid|quadruped|robotic|physical\s+ai|"
        r"autonomous\s+machines?)\b",
        re.I,
    )

    _AI_INFRASTRUCTURE_RE = re.compile(
        r"(?:"
        r"\bai\s+(?:accelerator|chip|compute|data\s*cent(?:er|re)|cluster|"
        r"infrastructure|capacity)\b|"
        r"\bgpu(?:s)?\b.{0,100}\b(?:ai|inference|training)\b|"
        r"\b(?:ai|inference|training)\b.{0,100}\bgpu(?:s)?\b|"
        r"\b(?:inference|training)\s+(?:accelerator|cluster|capacity|compute)\b|"
        r"\badvanced\s+packaging\b.{0,120}\b(?:ai|accelerator|gpu)\b|"
        r"\b(?:ai|accelerator|gpu)\b.{0,120}\badvanced\s+packaging\b"
        r")",
        re.I | re.S,
    )

    _GENERIC_INFRASTRUCTURE_RE = re.compile(
        r"\b(?:gpu(?:s)?|accelerator|semiconductor|advanced\s+packaging|nvlink|"
        r"data\s*cent(?:er|re)|compute\s+capacity|training\s+cluster|"
        r"inference\s+cluster|power\s+capacity)\b",
        re.I,
    )

    # Relation patterns require an explicit AI anchor.  Generic ``model``, GPU,
    # robot, etc. cannot by themselves create AI relevance.
    _AI_RELATION_ANCHOR = (
        r"(?:"
        r"\bartificial\s+intelligence\b|\bgenerative\s+ai\b|\bgenai\b|"
        r"\bagentic\s+ai\b|\bphysical\s+ai\b|\bmachine\s+learning\b|"
        r"\bdeep\s+learning\b|\blarge\s+language\s+models?\b|\bllms?\b|"
        r"\bfoundation\s+models?\b|\bai\s+agents?\b|\bai\s+models?\b|"
        r"\bai[- ](?:driven|enabled|powered|native|based)\b|"
        r"\bai\b|\banthropic\b|\bopenai\b|\bclaude\b|\bchatgpt\b|"
        r"\bcopilot\b|\bgemini\b|\bgpt(?:-[0-9a-z.]+)?\b"
        r")"
    )

    _CONSTRAINT_TERM = (
        r"(?:ban(?:s|ned|ning)?|restrict\w*|covered\s+list|approvals?|"
        r"authoriz\w*|authoris\w*|export\s+controls?|permit(?:s|ted|ting)?|"
        r"regulat\w*|sanction\w*|compliance|prohibit\w*|"
        r"limit(?:s|ed|ing|ation|ations)?)"
    )

    _CONSTRAINT_RELATION_RE = re.compile(
        rf"(?:"
        rf"{_AI_RELATION_ANCHOR}.{{0,120}}\b{_CONSTRAINT_TERM}\b|"
        rf"\b{_CONSTRAINT_TERM}\b.{{0,120}}{_AI_RELATION_ANCHOR}"
        rf")",
        re.I | re.S,
    )

    _CONSEQUENCE_VERB = (
        r"(?:drove|caused|increased|reduced|cut|boosted|delayed|"
        r"enabled|produced|identified)"
    )

    _CONSEQUENCE_RELATION_RE = re.compile(
        rf"(?:"
        rf"\b(?:due\s+to|because\s+of|driven\s+by|resulting\s+from|"
        rf"as\s+a\s+result\s+of)\b.{{0,140}}{_AI_RELATION_ANCHOR}|"
        rf"{_AI_RELATION_ANCHOR}.{{0,140}}\b{_CONSEQUENCE_VERB}\b"
        rf")",
        re.I | re.S,
    )

    _GENERIC_TOPIC_STOP = {
        "about",
        "latest",
        "news",
        "new",
        "development",
        "developments",
        "market",
        "industry",
        "company",
        "companies",
        "the",
        "and",
        "for",
        "with",
        "from",
    }

    @staticmethod
    def _clean(value: Any) -> str:
        if value is None:
            return ""
        return " ".join(str(value).split()).strip()

    @staticmethod
    def _matches(
        text: str,
        pattern: re.Pattern[str],
        *,
        signal_id: str,
        signal_family: str,
        strength: str,
    ) -> list[SignalMatch]:
        return [
            SignalMatch(
                signal_id=signal_id,
                signal_family=signal_family,
                strength=strength,
                span=text[match.start() : match.end()],
                start=match.start(),
                end=match.end(),
            )
            for match in pattern.finditer(text)
        ]

    @classmethod
    def is_broad_ai_topic(cls, topic: str) -> bool:
        value = cls._clean(topic).casefold()
        if value == "ai":
            return True
        return bool(cls._BROAD_AI_TOPIC_PHRASE_RE.search(value))

    @staticmethod
    def _dedupe(items: Sequence[SignalMatch]) -> tuple[SignalMatch, ...]:
        seen: set[tuple[str, int, int]] = set()
        output: list[SignalMatch] = []
        for item in sorted(items, key=lambda x: (x.start, x.end, x.signal_id)):
            key = (item.signal_id, item.start, item.end)
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
        return tuple(output)

    @classmethod
    def analyze(cls, *, topic: str, text: str) -> TopicSignalBundle:
        clean_text = cls._clean(text)
        broad_ai = cls.is_broad_ai_topic(topic)

        ai_signals: list[SignalMatch] = []
        robotics: list[SignalMatch] = []
        ai_infrastructure: list[SignalMatch] = []
        generic_infrastructure: list[SignalMatch] = []
        constraint: list[SignalMatch] = []
        consequence: list[SignalMatch] = []
        generic_topic: list[SignalMatch] = []

        if broad_ai:
            for signal_id, strength, pattern in cls._AI_PATTERNS:
                ai_signals.extend(
                    cls._matches(
                        clean_text,
                        pattern,
                        signal_id=signal_id,
                        signal_family="ai",
                        strength=strength,
                    )
                )

            robotics.extend(
                cls._matches(
                    clean_text,
                    cls._ROBOTICS_RE,
                    signal_id="robotics_or_physical_ai",
                    signal_family="robotics",
                    strength="medium",
                )
            )
            ai_infrastructure.extend(
                cls._matches(
                    clean_text,
                    cls._AI_INFRASTRUCTURE_RE,
                    signal_id="ai_infrastructure_relation",
                    signal_family="ai_infrastructure",
                    strength="medium",
                )
            )
            generic_infrastructure.extend(
                cls._matches(
                    clean_text,
                    cls._GENERIC_INFRASTRUCTURE_RE,
                    signal_id="generic_compute_infrastructure",
                    signal_family="generic_infrastructure",
                    strength="weak",
                )
            )
            constraint.extend(
                cls._matches(
                    clean_text,
                    cls._CONSTRAINT_RELATION_RE,
                    signal_id="ai_constraint_relation",
                    signal_family="constraint_relation",
                    strength="strong",
                )
            )
            consequence.extend(
                cls._matches(
                    clean_text,
                    cls._CONSEQUENCE_RELATION_RE,
                    signal_id="ai_consequence_relation",
                    signal_family="consequence_relation",
                    strength="strong",
                )
            )
        else:
            topic_value = cls._clean(topic).casefold()
            if topic_value:
                generic_topic.extend(
                    cls._matches(
                        clean_text,
                        re.compile(re.escape(topic_value), re.I),
                        signal_id="exact_topic_phrase",
                        signal_family="generic_topic",
                        strength="strong",
                    )
                )
                tokens = {
                    token
                    for token in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", topic_value)
                    if token not in cls._GENERIC_TOPIC_STOP
                }
                for token in sorted(tokens):
                    generic_topic.extend(
                        cls._matches(
                            clean_text,
                            re.compile(rf"\b{re.escape(token)}\b", re.I),
                            signal_id=f"topic_token:{token}",
                            signal_family="generic_topic",
                            strength="medium",
                        )
                    )

        material_action = cls._matches(
            clean_text,
            cls._MATERIAL_ACTION_RE,
            signal_id="material_action",
            signal_family="material_action",
            strength="strong",
        )

        return TopicSignalBundle(
            topic=cls._clean(topic),
            broad_ai_topic=broad_ai,
            text=clean_text,
            ai_signals=cls._dedupe(ai_signals),
            material_action_signals=cls._dedupe(material_action),
            robotics_signals=cls._dedupe(robotics),
            ai_infrastructure_signals=cls._dedupe(ai_infrastructure),
            generic_infrastructure_signals=cls._dedupe(generic_infrastructure),
            constraint_relation_signals=cls._dedupe(constraint),
            consequence_relation_signals=cls._dedupe(consequence),
            generic_topic_signals=cls._dedupe(generic_topic),
        )

    @classmethod
    def relevance_score(cls, bundle: TopicSignalBundle) -> float:
        """Legacy-compatible relevance baseline using the shared evidence bundle."""
        if bundle.broad_ai_topic:
            score = bundle.max_ai_strength_score

            if bundle.robotics_signals:
                score = max(score, 2.0)

            if bundle.ai_infrastructure_signals:
                score = max(score, 2.0)
            elif bundle.generic_infrastructure_signals:
                score = max(score, 1.0)

            # Defensive invariant: a relation can strengthen relevance only when
            # the same bundle contains explicit AI evidence.  The relation regexes
            # already enforce this; this second gate prevents future regex drift.
            if (
                bundle.has_explicit_ai_evidence
                and (
                    bundle.constraint_relation_signals
                    or bundle.consequence_relation_signals
                )
            ):
                score = max(score, 3.0)

            return round(score, 3)

        if not bundle.generic_topic_signals:
            return 0.0

        exact = any(
            item.signal_id == "exact_topic_phrase"
            for item in bundle.generic_topic_signals
        )
        if exact:
            return 3.0

        unique_tokens = {
            item.signal_id
            for item in bundle.generic_topic_signals
            if item.signal_id.startswith("topic_token:")
        }
        return round(min(3.0, len(unique_tokens) * 0.9), 3)

    @classmethod
    def linkage_type(
        cls,
        *,
        bundle: TopicSignalBundle,
        event_family: str = "",
    ) -> str:
        """Legacy-compatible linkage policy over the same signal bundle."""
        action = bundle.has_material_action

        if bundle.broad_ai_topic:
            # Relation labels require explicit AI evidence as a second defensive
            # invariant.  No generic ``model`` or other domain word can create a
            # material AI relation by itself.
            if (
                bundle.has_explicit_ai_evidence
                and bundle.constraint_relation_signals
                and action
            ):
                return "material_constraint"

            if (
                bundle.has_explicit_ai_evidence
                and bundle.consequence_relation_signals
                and action
            ):
                return "material_consequence"

            if bundle.ai_signals and action:
                return "direct"

            if bundle.robotics_signals and action:
                return "direct"

            if bundle.ai_infrastructure_signals and action:
                return "material_enabler"

            if (
                bundle.ai_signals
                or bundle.robotics_signals
                or bundle.ai_infrastructure_signals
            ):
                return "contextual"

            return "none"

        unique_tokens = {
            item.signal_id
            for item in bundle.generic_topic_signals
            if item.signal_id.startswith("topic_token:")
        }
        exact = any(
            item.signal_id == "exact_topic_phrase"
            for item in bundle.generic_topic_signals
        )

        if action and (exact or len(unique_tokens) >= 2):
            return "direct"

        if (
            action
            and unique_tokens
            and event_family in {"regulation", "litigation"}
        ):
            return "material_constraint"

        if bundle.generic_topic_signals:
            return "contextual"

        return "none"

    @classmethod
    def assess_shared_baseline(
        cls,
        *,
        topic: str,
        text: str,
        event_family: str = "",
    ) -> dict[str, Any]:
        bundle = cls.analyze(topic=topic, text=text)
        relevance = cls.relevance_score(bundle)
        linkage = cls.linkage_type(bundle=bundle, event_family=event_family)

        eligible_linkages = {
            "direct",
            "material_enabler",
            "material_constraint",
            "material_consequence",
        }

        topic_gate_pass = bool(
            relevance >= 2.0 and linkage in eligible_linkages
        )

        invariant_violation = bool(
            linkage in eligible_linkages
            and relevance < 2.0
        )

        invariant_violations = []
        if invariant_violation:
            invariant_violations.append(
                "strategic_linkage_with_relevance_below_threshold"
            )

        return {
            "service_version": SERVICE_VERSION,
            "relevance_score": relevance,
            "topic_linkage_type": linkage,
            "topic_gate_pass": topic_gate_pass,
            "invariant_violation": invariant_violation,
            "invariant_violations": invariant_violations,
            "signal_bundle": bundle.to_dict(),
        }
