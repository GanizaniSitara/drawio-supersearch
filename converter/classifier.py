"""
Diagram Type Classifier

Classifies diagrams into categories:
- network: Network topology, infrastructure diagrams
- application: Application architecture, system diagrams (candidates for C4)
- process: Business process flows, workflows, BPMN
- data_flow: Data flow diagrams, ETL pipelines
- org_chart: Organizational charts, team structures
- sequence: Sequence diagrams, interaction diagrams
- er_diagram: Entity-relationship diagrams, data models
- other: Uncategorized

Can classify from DrawIO XML content or from screenshots via Claude Vision.
"""

import os
import re
import json
import base64
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


DIAGRAM_TYPES = [
    "network",
    "application",
    "process",
    "data_flow",
    "org_chart",
    "sequence",
    "er_diagram",
    "other",
]

# Keywords for heuristic classification from DrawIO XML text
TYPE_KEYWORDS = {
    "network": {
        "server", "router", "switch", "firewall", "load balancer", "dns",
        "vpc", "subnet", "gateway", "network", "port", "ip", "tcp", "udp",
        "lan", "wan", "dmz", "vlan", "ethernet", "wifi", "proxy",
    },
    "application": {
        "api", "service", "microservice", "database", "web app", "frontend",
        "backend", "rest", "graphql", "container", "kubernetes", "docker",
        "lambda", "function", "queue", "cache", "redis", "kafka", "rabbitmq",
        "application", "system", "platform", "middleware", "gateway",
    },
    "process": {
        "start", "end", "decision", "process", "workflow", "step", "approve",
        "reject", "review", "submit", "notification", "email", "trigger",
        "condition", "loop", "parallel", "task", "activity", "bpmn",
    },
    "data_flow": {
        "etl", "extract", "transform", "load", "data", "pipeline",
        "source", "sink", "stream", "batch", "ingestion", "staging",
        "warehouse", "lake", "mart", "dimension", "fact",
    },
    "org_chart": {
        "manager", "director", "vp", "ceo", "cto", "cio", "team",
        "department", "division", "reports to", "head of", "lead",
        "engineer", "analyst", "architect",
    },
    "sequence": {
        "request", "response", "call", "return", "async", "sync",
        "message", "event", "publish", "subscribe", "actor", "lifeline",
    },
    "er_diagram": {
        "entity", "relationship", "attribute", "primary key", "foreign key",
        "one to many", "many to many", "table", "column", "index",
        "schema", "field", "constraint", "nullable",
    },
}


@dataclass
class ClassificationResult:
    """Result of diagram classification."""
    diagram_type: str = "other"
    confidence: float = 0.0
    scores: dict = None  # type -> score mapping
    is_architecture: bool = False  # True if suitable for C4 conversion
    method: str = "unknown"  # "heuristic", "ai", or "combined"

    def __post_init__(self):
        if self.scores is None:
            self.scores = {}


CLASSIFICATION_PROMPT = """Analyze this diagram and classify it into exactly ONE of these categories:

1. **network** - Network topology, infrastructure layout, cloud architecture (VPCs, subnets, servers)
2. **application** - Application/system architecture, microservices, software components, APIs
3. **process** - Business process flow, workflow, BPMN, decision trees
4. **data_flow** - Data pipelines, ETL flows, data movement between systems
5. **org_chart** - Organizational hierarchy, team structure, reporting lines
6. **sequence** - Sequence diagrams, interaction flows, message passing
7. **er_diagram** - Entity-relationship diagrams, database schemas, data models
8. **other** - Anything that doesn't fit the above categories

Also determine if this diagram represents a software architecture that could be converted to a C4 model (systems, containers, components with relationships).

Return ONLY a JSON object:
```json
{{"type": "application", "confidence": 0.85, "is_architecture": true, "reason": "Shows microservices with API connections"}}
```"""


class DiagramClassifier:
    """Classify diagrams by type using heuristics and/or AI."""

    def __init__(self, api_key=None, model="claude-haiku-4-5-20251001"):
        """
        Initialize classifier.

        Args:
            api_key: Anthropic API key (optional, for AI classification)
            model: Claude model. Defaults to Haiku for speed/cost.
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = None
        self.model = model

        if self.api_key and ANTHROPIC_AVAILABLE:
            self.client = anthropic.Anthropic(api_key=self.api_key)

    def classify_from_text(self, text_content):
        """
        Classify a diagram based on its text content (heuristic).

        Args:
            text_content: Extracted text from the diagram

        Returns:
            ClassificationResult
        """
        result = ClassificationResult(method="heuristic")
        text_lower = text_content.lower()

        # Score each type based on keyword matches
        scores = {}
        for dtype, keywords in TYPE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[dtype] = score

        result.scores = scores

        if not scores:
            result.diagram_type = "other"
            result.confidence = 0.3
            return result

        # Pick highest scoring type
        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]
        total_score = sum(scores.values())

        result.diagram_type = best_type
        result.confidence = min(0.9, best_score / max(total_score, 1) * 0.7 + 0.2)
        result.is_architecture = best_type in ("application", "network")

        return result

    def classify_from_screenshot(self, image_path):
        """
        Classify a diagram from its screenshot using Claude Vision.

        Args:
            image_path: Path to the diagram screenshot

        Returns:
            ClassificationResult
        """
        if not self.client:
            # Fall back to a low-confidence "other" if no API
            return ClassificationResult(
                diagram_type="other",
                confidence=0.1,
                method="none",
            )

        path = Path(image_path)
        if not path.exists():
            return ClassificationResult(
                diagram_type="other",
                confidence=0.0,
                method="error",
            )

        with open(path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        ext = path.suffix.lower()
        media_types = {
            ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }
        media_type = media_types.get(ext, "image/png")

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_data,
                                },
                            },
                            {"type": "text", "text": CLASSIFICATION_PROMPT},
                        ],
                    }
                ],
            )

            response_text = message.content[0].text
            json_match = re.search(r"\{[\s\S]*?\}", response_text)

            if json_match:
                data = json.loads(json_match.group())
                dtype = data.get("type", "other")
                if dtype not in DIAGRAM_TYPES:
                    dtype = "other"

                return ClassificationResult(
                    diagram_type=dtype,
                    confidence=data.get("confidence", 0.5),
                    is_architecture=data.get("is_architecture", False),
                    method="ai",
                )

        except Exception as e:
            logger.warning(f"AI classification failed: {e}")

        return ClassificationResult(
            diagram_type="other",
            confidence=0.2,
            method="ai_error",
        )

    def classify(self, text_content=None, image_path=None):
        """
        Classify a diagram using the best available method.

        Uses heuristic classification from text if available,
        AI vision classification from screenshot if available,
        or combines both for higher confidence.

        Args:
            text_content: Optional text extracted from the diagram
            image_path: Optional path to diagram screenshot

        Returns:
            ClassificationResult
        """
        heuristic_result = None
        ai_result = None

        if text_content:
            heuristic_result = self.classify_from_text(text_content)

        if image_path and self.client:
            ai_result = self.classify_from_screenshot(image_path)

        # Combine results if both available
        if heuristic_result and ai_result:
            # Trust AI more for type, but use both for confidence
            if ai_result.confidence > heuristic_result.confidence:
                result = ai_result
                result.method = "combined"
                result.confidence = min(
                    1.0,
                    (ai_result.confidence * 0.7 + heuristic_result.confidence * 0.3),
                )
                # Boost confidence if they agree
                if ai_result.diagram_type == heuristic_result.diagram_type:
                    result.confidence = min(1.0, result.confidence + 0.1)
            else:
                result = heuristic_result
                result.method = "combined"
            return result

        if ai_result:
            return ai_result

        if heuristic_result:
            return heuristic_result

        return ClassificationResult(
            diagram_type="other",
            confidence=0.1,
            method="none",
        )
