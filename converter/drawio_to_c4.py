"""
DrawIO → C4 Architecture Model Converter

Converts DrawIO diagrams that represent application/system architectures
into C4 model format (Context, Container, Component levels).

Uses Claude to analyze DrawIO XML and generate structured C4 models.

Usage:
    from converter.drawio_to_c4 import DrawioToC4Converter

    converter = DrawioToC4Converter(api_key="sk-...")
    c4_model = converter.convert("diagram.drawio")
    # c4_model.systems - list of systems
    # c4_model.containers - list of containers
    # c4_model.relationships - list of relationships
    # c4_model.to_drawio_c4() - render as DrawIO C4 diagram
"""

import os
import re
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


@dataclass
class C4Person:
    """A person/user in the C4 model."""
    name: str
    description: str = ""
    external: bool = False


@dataclass
class C4System:
    """A software system in the C4 model."""
    name: str
    description: str = ""
    technology: str = ""
    external: bool = False


@dataclass
class C4Container:
    """A container (application/service) within a system."""
    name: str
    system: str = ""
    description: str = ""
    technology: str = ""
    container_type: str = "application"  # application, database, queue, etc.


@dataclass
class C4Component:
    """A component within a container."""
    name: str
    container: str = ""
    description: str = ""
    technology: str = ""


@dataclass
class C4Relationship:
    """A relationship between C4 elements."""
    source: str
    target: str
    description: str = ""
    technology: str = ""
    direction: str = "forward"  # forward, back, both


@dataclass
class C4Model:
    """Complete C4 architecture model."""
    title: str = ""
    level: str = "context"  # context, container, component
    persons: list = field(default_factory=list)
    systems: list = field(default_factory=list)
    containers: list = field(default_factory=list)
    components: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    confidence: float = 0.0
    source_diagram: str = ""
    error: str = ""

    def to_dict(self):
        """Serialize to dict for JSON export."""
        return {
            "title": self.title,
            "level": self.level,
            "persons": [vars(p) for p in self.persons],
            "systems": [vars(s) for s in self.systems],
            "containers": [vars(c) for c in self.containers],
            "components": [vars(c) for c in self.components],
            "relationships": [vars(r) for r in self.relationships],
            "confidence": self.confidence,
            "source_diagram": self.source_diagram,
        }

    def to_json(self, indent=2):
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_drawio_c4(self):
        """Render the C4 model as a DrawIO diagram using C4 shapes."""
        cells = []
        cell_id = 2
        element_ids = {}  # name -> cell_id mapping

        y_offset = 40
        x_center = 500

        # Layout: persons at top, systems in middle, external at bottom
        # C4 Context level layout
        if self.persons:
            x = x_center - (len(self.persons) * 130) // 2
            for person in self.persons:
                cells.append(
                    f'        <mxCell id="{cell_id}" value="&lt;b&gt;{_xml_escape(person.name)}&lt;/b&gt;'
                    f'&lt;br/&gt;[Person]&lt;br/&gt;&lt;br/&gt;{_xml_escape(person.description)}" '
                    f'style="shape=mxgraph.c4.person2;whiteSpace=wrap;html=1;'
                    f'container=1;image;imageAspect=0;fillColor=#08427B;'
                    f'strokeColor=none;fontColor=#ffffff;align=center;fontSize=12;" '
                    f'vertex="1" parent="1">\n'
                    f'          <mxGeometry x="{x}" y="{y_offset}" width="200" height="180" as="geometry" />\n'
                    f'        </mxCell>'
                )
                element_ids[person.name] = cell_id
                cell_id += 1
                x += 260

        y_offset += 250

        # Internal systems
        internal_systems = [s for s in self.systems if not s.external]
        if internal_systems:
            x = x_center - (len(internal_systems) * 130) // 2
            for system in internal_systems:
                cells.append(
                    f'        <mxCell id="{cell_id}" value="&lt;b&gt;{_xml_escape(system.name)}&lt;/b&gt;'
                    f'&lt;br/&gt;[Software System]&lt;br/&gt;&lt;br/&gt;{_xml_escape(system.description)}'
                    f'{"&lt;br/&gt;&lt;i&gt;" + _xml_escape(system.technology) + "&lt;/i&gt;" if system.technology else ""}" '
                    f'style="rounded=1;whiteSpace=wrap;html=1;'
                    f'fillColor=#438DD5;strokeColor=none;fontColor=#ffffff;align=center;fontSize=12;" '
                    f'vertex="1" parent="1">\n'
                    f'          <mxGeometry x="{x}" y="{y_offset}" width="240" height="140" as="geometry" />\n'
                    f'        </mxCell>'
                )
                element_ids[system.name] = cell_id
                cell_id += 1
                x += 300

        y_offset += 220

        # Containers (if container level)
        if self.containers:
            x = x_center - (len(self.containers) * 140) // 2
            for container in self.containers:
                fill = "#438DD5"
                if container.container_type == "database":
                    shape = "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;"
                    fill = "#438DD5"
                elif container.container_type == "queue":
                    shape = "rounded=1;whiteSpace=wrap;html=1;"
                    fill = "#85BBF0"
                else:
                    shape = "rounded=1;whiteSpace=wrap;html=1;"

                cells.append(
                    f'        <mxCell id="{cell_id}" value="&lt;b&gt;{_xml_escape(container.name)}&lt;/b&gt;'
                    f'&lt;br/&gt;[Container: {_xml_escape(container.container_type)}]&lt;br/&gt;'
                    f'&lt;br/&gt;{_xml_escape(container.description)}'
                    f'{"&lt;br/&gt;&lt;i&gt;" + _xml_escape(container.technology) + "&lt;/i&gt;" if container.technology else ""}" '
                    f'style="{shape}'
                    f'fillColor={fill};strokeColor=none;fontColor=#ffffff;align=center;fontSize=11;" '
                    f'vertex="1" parent="1">\n'
                    f'          <mxGeometry x="{x}" y="{y_offset}" width="220" height="140" as="geometry" />\n'
                    f'        </mxCell>'
                )
                element_ids[container.name] = cell_id
                cell_id += 1
                x += 270

            y_offset += 220

        # External systems
        external_systems = [s for s in self.systems if s.external]
        if external_systems:
            x = x_center - (len(external_systems) * 130) // 2
            for system in external_systems:
                cells.append(
                    f'        <mxCell id="{cell_id}" value="&lt;b&gt;{_xml_escape(system.name)}&lt;/b&gt;'
                    f'&lt;br/&gt;[External System]&lt;br/&gt;&lt;br/&gt;{_xml_escape(system.description)}" '
                    f'style="rounded=1;whiteSpace=wrap;html=1;'
                    f'fillColor=#999999;strokeColor=none;fontColor=#ffffff;align=center;fontSize=12;" '
                    f'vertex="1" parent="1">\n'
                    f'          <mxGeometry x="{x}" y="{y_offset}" width="240" height="120" as="geometry" />\n'
                    f'        </mxCell>'
                )
                element_ids[system.name] = cell_id
                cell_id += 1
                x += 300

        # Relationships
        for rel in self.relationships:
            source_id = element_ids.get(rel.source)
            target_id = element_ids.get(rel.target)
            if source_id and target_id:
                label = _xml_escape(rel.description)
                if rel.technology:
                    label += f"&lt;br/&gt;&lt;i&gt;[{_xml_escape(rel.technology)}]&lt;/i&gt;"

                cells.append(
                    f'        <mxCell id="{cell_id}" value="{label}" '
                    f'style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;'
                    f'jetSize=auto;html=1;strokeColor=#707070;fontColor=#707070;fontSize=10;" '
                    f'edge="1" source="{source_id}" target="{target_id}" parent="1">\n'
                    f'          <mxGeometry relative="1" as="geometry" />\n'
                    f'        </mxCell>'
                )
                cell_id += 1

        # Build complete DrawIO file
        cells_str = "\n".join(cells)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net" modified="{datetime.now().isoformat()}" agent="DrawIO-SuperSearch-C4" version="24.0.0" type="device">
  <diagram id="c4-model" name="{_xml_escape(self.title or 'C4 Model')}">
    <mxGraphModel dx="1422" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1169" pageHeight="827" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
{cells_str}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


def _xml_escape(text):
    """Escape text for XML attribute values."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


C4_ANALYSIS_PROMPT = """You are an expert software architect. Analyze this DrawIO diagram XML and extract a C4 architecture model from it.

## DrawIO XML Content:
```xml
{xml_content}
```

## Instructions

Analyze the diagram and identify:

1. **People/Users** - Any human actors or user roles
2. **Software Systems** - Major applications, platforms, or systems (mark external ones)
3. **Containers** - If visible: applications, services, databases, queues, etc.
4. **Components** - If visible: internal components within containers
5. **Relationships** - Data flows, API calls, messages between elements

## Determine the C4 Level
- **Context**: Shows systems and people (high-level, no internal details)
- **Container**: Shows containers within a system (apps, databases, etc.)
- **Component**: Shows internal components of a container

## Output Format

Return a JSON object with this exact structure:
```json
{{
  "title": "System Name - C4 Level Diagram",
  "level": "context|container|component",
  "persons": [
    {{"name": "User", "description": "End user of the system", "external": false}}
  ],
  "systems": [
    {{"name": "System Name", "description": "What it does", "technology": "tech stack", "external": false}}
  ],
  "containers": [
    {{"name": "Web App", "system": "System Name", "description": "What it does", "technology": "React", "container_type": "application"}}
  ],
  "components": [
    {{"name": "Auth Module", "container": "Web App", "description": "Handles auth", "technology": "OAuth2"}}
  ],
  "relationships": [
    {{"source": "User", "target": "Web App", "description": "Uses", "technology": "HTTPS"}}
  ]
}}
```

## Important:
- container_type should be one of: application, database, queue, filesystem, browser, mobile
- Only include levels that are visible in the diagram
- If the diagram isn't an architecture diagram, set all lists to empty and title to "Not an architecture diagram"
- Return ONLY the JSON object, no other text"""

C4_VISION_PROMPT = """You are an expert software architect. Analyze this diagram screenshot and extract a C4 architecture model from it.

## Instructions

Look at this diagram and identify architectural elements:

1. **People/Users** - Any human actors, user roles, or stick figures
2. **Software Systems** - Major applications, platforms, services (blue/colored boxes usually)
3. **Containers** - Applications, services, databases, message queues
4. **Components** - Internal modules or components
5. **Relationships** - Arrows, lines, data flows between elements

## Output Format

Return a JSON object with this exact structure:
```json
{{
  "title": "System Name - C4 Level Diagram",
  "level": "context|container|component",
  "persons": [
    {{"name": "User", "description": "End user of the system", "external": false}}
  ],
  "systems": [
    {{"name": "System Name", "description": "What it does", "technology": "", "external": false}}
  ],
  "containers": [
    {{"name": "Web App", "system": "System Name", "description": "What it does", "technology": "React", "container_type": "application"}}
  ],
  "components": [],
  "relationships": [
    {{"source": "User", "target": "Web App", "description": "Uses", "technology": "HTTPS"}}
  ]
}}
```

## Important:
- container_type values: application, database, queue, filesystem, browser, mobile
- Only include C4 levels actually visible in the diagram
- If this is NOT an architecture/system diagram, return empty lists and title="Not an architecture diagram"
- Return ONLY the JSON object"""


class DrawioToC4Converter:
    """Convert DrawIO diagrams to C4 architecture models."""

    def __init__(self, api_key=None, model="claude-sonnet-4-20250514"):
        """
        Initialize converter.

        Args:
            api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
            model: Claude model to use.
        """
        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic package required. Install with: pip install anthropic"
            )

        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY environment "
                "variable or pass api_key parameter."
            )

        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = model

    def _extract_text_from_drawio(self, drawio_path):
        """Extract readable XML content from DrawIO file."""
        try:
            tree = ET.parse(drawio_path)
            # Return the full XML as string for Claude to analyze
            return ET.tostring(tree.getroot(), encoding="unicode")
        except ET.ParseError as e:
            logger.warning(f"Failed to parse DrawIO XML: {e}")
            with open(drawio_path, "r", encoding="utf-8") as f:
                return f.read()

    def _parse_c4_response(self, response_text):
        """Parse Claude's JSON response into a C4Model."""
        model = C4Model()

        # Extract JSON from response
        json_match = re.search(r"\{[\s\S]*\}", response_text)
        if not json_match:
            model.error = "No JSON found in response"
            return model

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            model.error = f"Invalid JSON: {e}"
            return model

        model.title = data.get("title", "")
        model.level = data.get("level", "context")

        for p in data.get("persons", []):
            model.persons.append(C4Person(
                name=p.get("name", ""),
                description=p.get("description", ""),
                external=p.get("external", False),
            ))

        for s in data.get("systems", []):
            model.systems.append(C4System(
                name=s.get("name", ""),
                description=s.get("description", ""),
                technology=s.get("technology", ""),
                external=s.get("external", False),
            ))

        for c in data.get("containers", []):
            model.containers.append(C4Container(
                name=c.get("name", ""),
                system=c.get("system", ""),
                description=c.get("description", ""),
                technology=c.get("technology", ""),
                container_type=c.get("container_type", "application"),
            ))

        for c in data.get("components", []):
            model.components.append(C4Component(
                name=c.get("name", ""),
                container=c.get("container", ""),
                description=c.get("description", ""),
                technology=c.get("technology", ""),
            ))

        for r in data.get("relationships", []):
            model.relationships.append(C4Relationship(
                source=r.get("source", ""),
                target=r.get("target", ""),
                description=r.get("description", ""),
                technology=r.get("technology", ""),
            ))

        # Calculate confidence
        total_elements = (
            len(model.persons) + len(model.systems)
            + len(model.containers) + len(model.components)
        )
        if model.title == "Not an architecture diagram":
            model.confidence = 0.0
        elif total_elements == 0:
            model.confidence = 0.1
        else:
            model.confidence = min(
                1.0,
                0.3
                + (0.2 if len(model.systems) >= 2 else 0.1 * len(model.systems))
                + (0.2 if len(model.relationships) >= 2 else 0.1 * len(model.relationships))
                + (0.15 if model.persons else 0)
                + (0.15 if model.containers else 0),
            )

        return model

    def convert_from_xml(self, drawio_path, extra_context=""):
        """
        Convert a DrawIO XML file to C4 model.

        Args:
            drawio_path: Path to .drawio file
            extra_context: Optional context about the diagram

        Returns:
            C4Model
        """
        xml_content = self._extract_text_from_drawio(drawio_path)

        prompt = C4_ANALYSIS_PROMPT.format(xml_content=xml_content)
        if extra_context:
            prompt += f"\n\n## Additional Context\n{extra_context}"

        logger.info(f"Analyzing {drawio_path} for C4 model extraction...")

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = message.content[0].text
            model = self._parse_c4_response(response_text)
            model.source_diagram = str(drawio_path)
            return model

        except Exception as e:
            model = C4Model()
            model.error = str(e)
            model.source_diagram = str(drawio_path)
            return model

    def convert_from_screenshot(self, image_path, extra_context=""):
        """
        Convert a diagram screenshot directly to C4 model (bypassing DrawIO).

        Args:
            image_path: Path to screenshot PNG
            extra_context: Optional context

        Returns:
            C4Model
        """
        import base64

        path = Path(image_path)
        if not path.exists():
            model = C4Model()
            model.error = f"Image not found: {image_path}"
            return model

        with open(path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        ext = path.suffix.lower()
        media_types = {
            ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = media_types.get(ext, "image/png")

        prompt = C4_VISION_PROMPT
        if extra_context:
            prompt += f"\n\n## Additional Context\n{extra_context}"

        logger.info(f"Analyzing screenshot {image_path} for C4 model...")

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
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
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )

            response_text = message.content[0].text
            model = self._parse_c4_response(response_text)
            model.source_diagram = str(image_path)
            return model

        except Exception as e:
            model = C4Model()
            model.error = str(e)
            model.source_diagram = str(image_path)
            return model

    def convert_and_save(self, input_path, output_dir=None):
        """
        Convert and save both C4 JSON model and C4 DrawIO diagram.

        Args:
            input_path: Path to .drawio file or screenshot
            output_dir: Output directory. Defaults to same as input.

        Returns:
            C4Model
        """
        path = Path(input_path)

        if path.suffix.lower() == ".drawio":
            model = self.convert_from_xml(input_path)
        else:
            model = self.convert_from_screenshot(input_path)

        if model.error or model.confidence == 0.0:
            return model

        if output_dir is None:
            output_dir = path.parent

        os.makedirs(output_dir, exist_ok=True)
        base_name = path.stem

        # Save JSON model
        json_path = os.path.join(output_dir, f"{base_name}.c4.json")
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(model.to_json())
        logger.info(f"Saved C4 model: {json_path}")

        # Save C4 DrawIO diagram
        if model.systems or model.containers:
            drawio_path = os.path.join(output_dir, f"{base_name}.c4.drawio")
            with open(drawio_path, "w", encoding="utf-8") as f:
                f.write(model.to_drawio_c4())
            logger.info(f"Saved C4 DrawIO: {drawio_path}")

        return model
