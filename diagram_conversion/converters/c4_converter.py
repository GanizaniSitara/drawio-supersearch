"""
C4 Model Converter: DrawIO XML → C4 Architecture Model

Converts application/system DrawIO diagrams into C4 architecture models
following Simon Brown's C4 model specification:
- Context level: Systems and external actors
- Container level: Applications, data stores, services within a system
- Component level: Components within a container
"""

import os
import json
import base64
import logging
import xml.etree.ElementTree as ET
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

C4_CONVERSION_PROMPT = """You are an expert software architect specializing in the C4 model (by Simon Brown).

Analyze this DrawIO diagram (provided as XML) and convert it into a C4 architecture model.

INPUT DrawIO XML:
{drawio_xml}

Determine the appropriate C4 level (Context, Container, or Component) and extract a structured model.

OUTPUT FORMAT: Return ONLY a JSON object with this structure:

{{
    "c4_level": "context|container|component",
    "title": "<diagram title>",
    "description": "<one-sentence description>",
    "systems": [
        {{
            "id": "<unique_id>",
            "name": "<system name>",
            "type": "system|external_system|person|container|component|database|queue|service",
            "description": "<what this does>",
            "technology": "<technology stack if visible>",
            "is_external": false,
            "tags": ["<tag1>", "<tag2>"]
        }}
    ],
    "relationships": [
        {{
            "source_id": "<source system id>",
            "target_id": "<target system id>",
            "description": "<what data/interaction flows>",
            "technology": "<protocol/technology if visible>",
            "is_bidirectional": false
        }}
    ],
    "boundaries": [
        {{
            "id": "<boundary_id>",
            "name": "<boundary name>",
            "type": "enterprise|system|container",
            "contains": ["<system_id1>", "<system_id2>"]
        }}
    ],
    "metadata": {{
        "confidence": 0.0,
        "notes": "<any conversion notes or ambiguities>",
        "unmapped_elements": ["<elements that couldn't be classified>"]
    }}
}}

RULES:
1. Every visible named element should appear in "systems"
2. Every connection/arrow should appear in "relationships"
3. Use the type field to distinguish between people, systems, databases, queues, etc.
4. Mark external systems (outside the organization boundary) with is_external=true
5. If elements are grouped (in a box/boundary), capture that in "boundaries"
6. Be specific about technology stacks when visible (Java, .NET, PostgreSQL, Kafka, etc.)
7. Generate stable IDs based on the system names (e.g., "sys_order_service")
"""

C4_FROM_IMAGE_PROMPT = """You are an expert software architect specializing in the C4 model (by Simon Brown).

Analyze this diagram screenshot and extract a C4 architecture model from it.

Determine the appropriate C4 level (Context, Container, or Component) and extract a structured model.

OUTPUT FORMAT: Return ONLY a JSON object with this structure:

{
    "c4_level": "context|container|component",
    "title": "<diagram title>",
    "description": "<one-sentence description>",
    "systems": [
        {
            "id": "<unique_id>",
            "name": "<system name>",
            "type": "system|external_system|person|container|component|database|queue|service",
            "description": "<what this does>",
            "technology": "<technology stack if visible>",
            "is_external": false,
            "tags": ["<tag1>", "<tag2>"]
        }
    ],
    "relationships": [
        {
            "source_id": "<source system id>",
            "target_id": "<target system id>",
            "description": "<what data/interaction flows>",
            "technology": "<protocol/technology if visible>",
            "is_bidirectional": false
        }
    ],
    "boundaries": [
        {
            "id": "<boundary_id>",
            "name": "<boundary name>",
            "type": "enterprise|system|container",
            "contains": ["<system_id1>", "<system_id2>"]
        }
    ],
    "metadata": {
        "confidence": 0.0,
        "notes": "<any conversion notes or ambiguities>",
        "unmapped_elements": ["<elements that couldn't be classified>"]
    }
}

RULES:
1. Every visible named element should appear in "systems"
2. Every connection/arrow should appear in "relationships"
3. Use the type field to distinguish between people, systems, databases, queues, etc.
4. Mark external systems with is_external=true
5. If elements are grouped in a box/boundary, capture in "boundaries"
6. Be specific about technology stacks when visible
7. Generate stable IDs based on the system names (e.g., "sys_order_service")
"""


@dataclass
class C4Model:
    """A C4 architecture model."""

    c4_level: str = "context"
    title: str = ""
    description: str = ""
    systems: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    boundaries: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    source_path: str = ""

    def to_dict(self) -> dict:
        return {
            "c4_level": self.c4_level,
            "title": self.title,
            "description": self.description,
            "systems": self.systems,
            "relationships": self.relationships,
            "boundaries": self.boundaries,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict, source_path: str = "") -> "C4Model":
        return cls(
            c4_level=data.get("c4_level", "context"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            systems=data.get("systems", []),
            relationships=data.get("relationships", []),
            boundaries=data.get("boundaries", []),
            metadata=data.get("metadata", {}),
            source_path=source_path,
        )

    def get_system_names(self) -> list[str]:
        return [s["name"] for s in self.systems if s.get("name")]

    def get_technologies(self) -> list[str]:
        techs = set()
        for s in self.systems:
            if s.get("technology"):
                techs.add(s["technology"])
        for r in self.relationships:
            if r.get("technology"):
                techs.add(r["technology"])
        return sorted(techs)

    def to_drawio_c4(self) -> str:
        """Generate DrawIO XML using C4 shape library."""
        cells = []
        cell_id = 2  # 0 and 1 are reserved

        # Position tracking for layout
        x, y = 50, 50
        col_width = 250
        row_height = 200
        cols = 4
        col = 0

        # Boundary boxes first
        boundary_map = {}
        for boundary in self.boundaries:
            bid = f"boundary_{cell_id}"
            boundary_map[boundary.get("id", "")] = bid
            contains = boundary.get("contains", [])
            bw = max(col_width * 2, col_width * len(contains))
            bh = row_height + 80
            cells.append(
                f'        <mxCell id="{bid}" value="{boundary.get("name", "")}" '
                f'style="rounded=1;whiteSpace=wrap;html=1;dashed=1;dashPattern=5 5;'
                f'fillColor=none;strokeColor=#666666;fontSize=14;fontStyle=1;'
                f'verticalAlign=top;spacingTop=8;" '
                f'vertex="1" parent="1">'
                f'\n          <mxGeometry x="{x}" y="{y}" width="{bw}" height="{bh}" as="geometry" />'
                f"\n        </mxCell>"
            )
            cell_id += 1
            y += bh + 40

        # Reset for systems
        x, y = 80, 130
        col = 0
        system_cell_map = {}

        for system in self.systems:
            sid = str(cell_id)
            system_cell_map[system.get("id", "")] = sid

            stype = system.get("type", "system")
            name = system.get("name", "Unknown")
            desc = system.get("description", "")
            tech = system.get("technology", "")
            is_ext = system.get("is_external", False)

            label = f"<b>{name}</b>"
            if tech:
                label += f"<br/><i>[{tech}]</i>"
            if desc:
                label += f"<br/><font style='font-size:10px'>{desc}</font>"

            # Choose style based on type
            if stype == "person":
                style = ("shape=mxgraph.c4.person2;whiteSpace=wrap;html=1;"
                         "fillColor=#08427B;fontColor=#ffffff;align=center;")
            elif stype == "database":
                style = ("shape=cylinder3;whiteSpace=wrap;html=1;size=15;"
                         "fillColor=#438DD5;fontColor=#ffffff;strokeColor=#3C7FC0;")
            elif stype == "queue":
                style = ("shape=mxgraph.c4.queues;whiteSpace=wrap;html=1;"
                         "fillColor=#438DD5;fontColor=#ffffff;")
            elif is_ext or stype == "external_system":
                style = ("rounded=1;whiteSpace=wrap;html=1;"
                         "fillColor=#999999;fontColor=#ffffff;strokeColor=#8C8C8C;")
            else:
                style = ("rounded=1;whiteSpace=wrap;html=1;"
                         "fillColor=#438DD5;fontColor=#ffffff;strokeColor=#3C7FC0;")

            w, h = 200, 120
            if stype == "person":
                w, h = 160, 140

            cells.append(
                f'        <mxCell id="{sid}" value="{label}" '
                f'style="{style}" vertex="1" parent="1">'
                f'\n          <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />'
                f"\n        </mxCell>"
            )
            cell_id += 1

            col += 1
            if col >= cols:
                col = 0
                x = 80
                y += row_height
            else:
                x += col_width

        # Relationships as edges
        for rel in self.relationships:
            source_id = system_cell_map.get(rel.get("source_id", ""), "")
            target_id = system_cell_map.get(rel.get("target_id", ""), "")
            if not source_id or not target_id:
                continue

            label = rel.get("description", "")
            tech = rel.get("technology", "")
            if tech:
                label += f" [{tech}]" if label else f"[{tech}]"

            style = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;"
            if rel.get("is_bidirectional"):
                style += "startArrow=classic;endArrow=classic;"

            cells.append(
                f'        <mxCell id="{cell_id}" value="{label}" '
                f'style="{style}" edge="1" '
                f'source="{source_id}" target="{target_id}" parent="1">'
                f"\n          <mxGeometry relative=\"1\" as=\"geometry\" />"
                f"\n        </mxCell>"
            )
            cell_id += 1

        cells_xml = "\n".join(cells)

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net" agent="c4-converter" version="1.0">
  <diagram id="c4-{self.c4_level}" name="{self.title or 'C4 Diagram'}">
    <mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1169" pageHeight="827">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
{cells_xml}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


@dataclass
class C4ConversionResult:
    """Result of C4 conversion."""

    source_path: str
    model: Optional[C4Model] = None
    success: bool = False
    error: str = ""
    tokens_used: int = 0


class C4Converter:
    """Converts diagrams into C4 architecture models."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514",
                 max_tokens: int = 4096):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens

    def _call_api(self, messages: list) -> dict:
        """Call Claude API."""
        import httpx

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0.0,
            "messages": messages,
        }

        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json()

    def _parse_model_response(self, response_text: str,
                              source_path: str) -> C4Model:
        """Parse API response into a C4Model."""
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])

        data = json.loads(text)
        return C4Model.from_dict(data, source_path=source_path)

    def convert_from_drawio(self, drawio_xml: str,
                            source_path: str = "") -> C4ConversionResult:
        """
        Convert DrawIO XML to a C4 model.

        Args:
            drawio_xml: The DrawIO XML content
            source_path: Path to the source file (for reference)

        Returns:
            C4ConversionResult
        """
        result = C4ConversionResult(source_path=source_path)

        try:
            prompt = C4_CONVERSION_PROMPT.format(drawio_xml=drawio_xml)
            messages = [{"role": "user", "content": prompt}]

            api_response = self._call_api(messages)

            response_text = ""
            for block in api_response.get("content", []):
                if block.get("type") == "text":
                    response_text += block["text"]

            usage = api_response.get("usage", {})
            result.tokens_used = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

            result.model = self._parse_model_response(response_text, source_path)
            result.success = True

            logger.info(
                f"C4 converted: {result.model.title} "
                f"({len(result.model.systems)} systems, "
                f"{len(result.model.relationships)} relationships)"
            )

        except json.JSONDecodeError as e:
            result.error = f"Failed to parse C4 model: {e}"
            logger.error(f"C4 JSON error: {e}")
        except Exception as e:
            result.error = str(e)
            logger.error(f"C4 conversion failed: {e}")

        return result

    def convert_from_image(self, image_path: str) -> C4ConversionResult:
        """
        Convert a diagram screenshot directly to C4 (skipping DrawIO intermediate).

        Args:
            image_path: Path to the screenshot

        Returns:
            C4ConversionResult
        """
        result = C4ConversionResult(source_path=image_path)

        try:
            ext = os.path.splitext(image_path)[1].lower()
            media_type_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }
            media_type = media_type_map.get(ext, "image/png")

            with open(image_path, "rb") as f:
                image_data = base64.standard_b64encode(f.read()).decode("utf-8")

            messages = [
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
                        {"type": "text", "text": C4_FROM_IMAGE_PROMPT},
                    ],
                }
            ]

            api_response = self._call_api(messages)

            response_text = ""
            for block in api_response.get("content", []):
                if block.get("type") == "text":
                    response_text += block["text"]

            usage = api_response.get("usage", {})
            result.tokens_used = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

            result.model = self._parse_model_response(response_text, image_path)
            result.success = True

        except Exception as e:
            result.error = str(e)
            logger.error(f"C4 image conversion failed: {e}")

        return result
