"""
Diagram Classifier: Detect diagram type from screenshots.

Classifies diagrams into categories:
- network: Network topology, infrastructure diagrams
- application: Application architecture, system diagrams
- process: Business process flows, workflows
- data_flow: Data flow diagrams, ETL pipelines
- org_chart: Organizational charts
- sequence: Sequence diagrams, timing diagrams
- er_diagram: Entity-relationship diagrams
- other: Unclassified
"""

import os
import json
import base64
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

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

CLASSIFICATION_PROMPT = """Analyze this diagram screenshot and classify it into ONE of the following categories:

1. **network** - Network topology, infrastructure diagrams showing servers, switches, firewalls, VPNs, load balancers, subnets
2. **application** - Application/system architecture showing software components, services, APIs, microservices, databases, message queues
3. **process** - Business process flows, workflows, BPMN diagrams, swim-lane diagrams, decision trees
4. **data_flow** - Data flow diagrams, ETL pipelines, data lineage, integration flows
5. **org_chart** - Organizational charts, team structures, reporting hierarchies
6. **sequence** - Sequence diagrams, timing diagrams, interaction diagrams
7. **er_diagram** - Entity-relationship diagrams, database schemas, data models
8. **other** - Anything that doesn't fit the above categories

Respond with ONLY a JSON object in this exact format:
{
    "type": "<category>",
    "confidence": <0.0-1.0>,
    "description": "<one-sentence description of what the diagram shows>",
    "key_elements": ["<element1>", "<element2>", ...],
    "is_system_diagram": <true/false>,
    "c4_convertible": <true/false>
}

Rules:
- "is_system_diagram" should be true if the diagram shows software systems, applications, or technical infrastructure
- "c4_convertible" should be true if the diagram could meaningfully be converted to a C4 architecture model (system context, container, or component level)
- "key_elements" should list the main named elements/systems visible in the diagram (up to 10)
- Be conservative with confidence - only use >0.8 if you're very sure
"""


@dataclass
class ClassificationResult:
    """Result of diagram classification."""

    source_path: str
    diagram_type: str = "other"
    confidence: float = 0.0
    description: str = ""
    key_elements: list = field(default_factory=list)
    is_system_diagram: bool = False
    c4_convertible: bool = False
    error: str = ""
    tokens_used: int = 0


class DiagramClassifier:
    """Classifies diagram screenshots by type using Claude Vision."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514",
                 max_tokens: int = 1024):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens

    def _encode_image(self, image_path: str) -> tuple[str, str]:
        """Read and base64 encode an image."""
        ext = os.path.splitext(image_path)[1].lower()
        media_type_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = media_type_map.get(ext, "image/png")
        with open(image_path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")
        return data, media_type

    def _call_api(self, image_data: str, media_type: str,
                  extra_context: str = "") -> dict:
        """Call Claude API for classification."""
        import httpx

        prompt = CLASSIFICATION_PROMPT
        if extra_context:
            prompt += f"\n\nAdditional context: {extra_context}"

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0.0,
            "messages": [
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
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }

        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=60.0,
        )
        response.raise_for_status()
        return response.json()

    def classify(self, image_path: str,
                 extra_context: str = "") -> ClassificationResult:
        """
        Classify a diagram screenshot.

        Args:
            image_path: Path to the screenshot
            extra_context: Optional page title or metadata context

        Returns:
            ClassificationResult
        """
        result = ClassificationResult(source_path=image_path)

        if not os.path.exists(image_path):
            result.error = f"Image not found: {image_path}"
            return result

        try:
            image_data, media_type = self._encode_image(image_path)
            api_response = self._call_api(image_data, media_type, extra_context)

            response_text = ""
            for block in api_response.get("content", []):
                if block.get("type") == "text":
                    response_text += block["text"]

            usage = api_response.get("usage", {})
            result.tokens_used = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

            # Parse JSON response
            # Handle potential markdown code blocks
            text = response_text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])

            parsed = json.loads(text)

            result.diagram_type = parsed.get("type", "other")
            if result.diagram_type not in DIAGRAM_TYPES:
                result.diagram_type = "other"

            result.confidence = float(parsed.get("confidence", 0.0))
            result.description = parsed.get("description", "")
            result.key_elements = parsed.get("key_elements", [])
            result.is_system_diagram = bool(parsed.get("is_system_diagram", False))
            result.c4_convertible = bool(parsed.get("c4_convertible", False))

            logger.info(
                f"Classified {os.path.basename(image_path)}: "
                f"{result.diagram_type} (confidence={result.confidence:.2f}, "
                f"c4={result.c4_convertible})"
            )

        except json.JSONDecodeError as e:
            result.error = f"Failed to parse classification response: {e}"
            logger.error(f"Classification JSON error for {image_path}: {e}")
        except Exception as e:
            result.error = str(e)
            logger.error(f"Classification failed for {image_path}: {e}")

        return result

    def classify_from_text(self, diagram_name: str, page_title: str = "",
                           body_text: str = "") -> ClassificationResult:
        """
        Quick heuristic classification based on text metadata alone (no API call).
        Useful for pre-filtering before spending API credits.

        Returns a low-confidence result based on keyword matching.
        """
        result = ClassificationResult(source_path="")
        search_text = f"{diagram_name} {page_title} {body_text}".lower()

        # Keyword-based heuristics
        scores = {
            "network": 0.0,
            "application": 0.0,
            "process": 0.0,
            "data_flow": 0.0,
            "org_chart": 0.0,
            "sequence": 0.0,
            "er_diagram": 0.0,
        }

        network_kw = ["network", "topology", "firewall", "switch", "router",
                       "vpn", "subnet", "vlan", "load balancer", "dns",
                       "infrastructure", "server", "rack", "datacenter"]
        app_kw = ["application", "architecture", "system", "service",
                   "microservice", "api", "component", "container",
                   "deployment", "kubernetes", "docker", "aws", "azure"]
        process_kw = ["process", "workflow", "bpmn", "swimlane", "swim lane",
                       "decision", "approval", "flow chart", "flowchart",
                       "business process", "procedure"]
        data_kw = ["data flow", "etl", "pipeline", "data lineage",
                    "integration", "kafka", "data warehouse", "ingestion"]
        org_kw = ["org chart", "organization", "team structure",
                   "reporting", "hierarchy", "department"]
        seq_kw = ["sequence", "interaction", "timing", "message flow"]
        er_kw = ["entity", "relationship", "er diagram", "database schema",
                  "data model", "table", "foreign key", "primary key"]

        for kw in network_kw:
            if kw in search_text:
                scores["network"] += 1
        for kw in app_kw:
            if kw in search_text:
                scores["application"] += 1
        for kw in process_kw:
            if kw in search_text:
                scores["process"] += 1
        for kw in data_kw:
            if kw in search_text:
                scores["data_flow"] += 1
        for kw in org_kw:
            if kw in search_text:
                scores["org_chart"] += 1
        for kw in seq_kw:
            if kw in search_text:
                scores["sequence"] += 1
        for kw in er_kw:
            if kw in search_text:
                scores["er_diagram"] += 1

        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]

        if best_score > 0:
            result.diagram_type = best_type
            result.confidence = min(0.3 + (best_score * 0.1), 0.6)
            result.is_system_diagram = best_type in ("network", "application", "data_flow")
            result.c4_convertible = best_type in ("application", "data_flow")
        else:
            result.diagram_type = "other"
            result.confidence = 0.1

        return result
