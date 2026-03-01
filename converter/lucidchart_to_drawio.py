"""
Lucidchart Screenshot → DrawIO XML Converter

Uses Claude Vision API to analyze Lucidchart diagram screenshots and
generate equivalent DrawIO XML representations.

The conversion pipeline:
1. Send screenshot to Claude Vision with detailed prompt
2. Claude analyzes shapes, text, connections, layout
3. Claude generates DrawIO-compatible XML
4. Post-process and validate the XML

Usage:
    from converter.lucidchart_to_drawio import LucidchartToDrawioConverter

    converter = LucidchartToDrawioConverter(api_key="sk-...")
    result = converter.convert("screenshot.png")
    # result.drawio_xml - the generated XML
    # result.confidence - quality confidence score 0-1
    # result.shapes_detected - number of shapes found
"""

import os
import re
import json
import base64
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


@dataclass
class ConversionResult:
    """Result of a Lucidchart → DrawIO conversion."""
    drawio_xml: str = ""
    confidence: float = 0.0
    shapes_detected: int = 0
    connections_detected: int = 0
    text_elements: list = field(default_factory=list)
    diagram_type: str = "unknown"
    error: str = ""
    raw_response: str = ""


# DrawIO XML template for wrapping generated content
DRAWIO_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net" modified="{modified}" agent="DrawIO-SuperSearch-Converter" version="24.0.0" type="device">
  <diagram id="converted" name="{name}">
    <mxGraphModel dx="1422" dy="762" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1169" pageHeight="827" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
{cells}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""

VISION_PROMPT = """You are an expert at converting diagram screenshots into DrawIO XML format. Analyze this diagram screenshot and recreate it as DrawIO mxCell XML elements.

## Instructions

1. **Identify all visual elements:**
   - Shapes (rectangles, circles, diamonds, cylinders, clouds, hexagons, etc.)
   - Text labels on shapes and standalone text
   - Connections/arrows between shapes (including labels on connections)
   - Groups/containers that hold other elements
   - Icons or special symbols

2. **For each shape, determine:**
   - Shape type (rectangle, ellipse, rhombus, cylinder, cloud, etc.)
   - Position (x, y) and dimensions (width, height)
   - Text/label content
   - Fill color (use hex codes)
   - Border/stroke color and style
   - Font size and style

3. **For each connection, determine:**
   - Source and target shapes
   - Arrow style (solid, dashed, etc.)
   - Arrow direction (one-way, two-way, none)
   - Label text if any

4. **Preserve the layout** as closely as possible to the original screenshot.

## Output Format

Return ONLY valid DrawIO mxCell XML elements. Each element must have a unique numeric ID starting from 2. Use parent="1" for top-level elements.

### Shape examples:
```xml
<mxCell id="2" value="Server" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;" vertex="1" parent="1">
  <mxGeometry x="100" y="50" width="120" height="60" as="geometry" />
</mxCell>
```

### Connection examples:
```xml
<mxCell id="10" value="HTTP" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jetSize=auto;html=1;" edge="1" source="2" target="3" parent="1">
  <mxGeometry relative="1" as="geometry" />
</mxCell>
```

### Common DrawIO style mappings:
- Rectangle: `rounded=0;whiteSpace=wrap;html=1;`
- Rounded rectangle: `rounded=1;whiteSpace=wrap;html=1;`
- Circle/Ellipse: `ellipse;whiteSpace=wrap;html=1;`
- Diamond: `rhombus;whiteSpace=wrap;html=1;`
- Cylinder (database): `shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;`
- Cloud: `ellipse;shape=cloud;whiteSpace=wrap;html=1;`
- Hexagon: `shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;`
- Person/Actor: `shape=mxgraph.basic.person;whiteSpace=wrap;html=1;`
- Document: `shape=document;whiteSpace=wrap;html=1;boundedLbl=1;`
- Process: `shape=process;whiteSpace=wrap;html=1;`
- Container/Group: `swimlane;whiteSpace=wrap;html=1;` (children use parent="containerID")

### Color mappings for common Lucidchart themes:
- Blue: fillColor=#dae8fc;strokeColor=#6c8ebf;
- Green: fillColor=#d5e8d4;strokeColor=#82b366;
- Orange: fillColor=#ffe6cc;strokeColor=#d6b656;
- Red: fillColor=#f8cecc;strokeColor=#b85450;
- Purple: fillColor=#e1d5e7;strokeColor=#9673a6;
- Yellow: fillColor=#fff2cc;strokeColor=#d6b656;
- Gray: fillColor=#f5f5f5;strokeColor=#666666;
- Dark: fillColor=#333333;strokeColor=#000000;fontColor=#ffffff;

## Important Notes
- Use the full width/height of a standard A4-landscape page (1169 x 827) for layout
- Scale shapes and positions proportionally to fill the page
- Preserve relative positioning between elements
- Include ALL text visible in the diagram
- For text-heavy elements, adjust width/height to fit content
- Return ONLY the mxCell XML elements, no wrapping tags"""


class LucidchartToDrawioConverter:
    """Convert Lucidchart screenshots to DrawIO XML using Claude Vision."""

    def __init__(self, api_key=None, model="claude-sonnet-4-20250514"):
        """
        Initialize converter.

        Args:
            api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
            model: Claude model to use. Defaults to Sonnet for cost efficiency.
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

    def _load_image_as_base64(self, image_path):
        """Load image file and return base64 encoded string."""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        with open(path, "rb") as f:
            data = f.read()

        return base64.standard_b64encode(data).decode("utf-8")

    def _get_media_type(self, image_path):
        """Determine media type from file extension."""
        ext = Path(image_path).suffix.lower()
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        return media_types.get(ext, "image/png")

    def _extract_xml_from_response(self, response_text):
        """Extract mxCell XML from Claude's response, handling code blocks."""
        # Try to extract from code blocks first
        code_block = re.search(r"```(?:xml)?\s*\n?(.*?)\n?```", response_text, re.DOTALL)
        if code_block:
            xml_content = code_block.group(1).strip()
        else:
            xml_content = response_text.strip()

        # Remove any wrapping tags that aren't mxCell
        # Keep only mxCell elements
        cells = re.findall(
            r"<mxCell\s[^>]*(?:/>|>.*?</mxCell>)",
            xml_content,
            re.DOTALL,
        )

        if not cells:
            # Try to find any XML-like content
            if "<mxCell" in xml_content:
                return xml_content
            return ""

        return "\n".join(cells)

    def _count_elements(self, xml_content):
        """Count shapes and connections in the XML."""
        shapes = 0
        connections = 0
        text_elements = []

        for match in re.finditer(r'<mxCell\s([^>]*?)(?:/>|>)', xml_content):
            attrs = match.group(1)
            value_match = re.search(r'value="([^"]*)"', attrs)
            value = value_match.group(1) if value_match else ""

            if 'edge="1"' in attrs:
                connections += 1
            elif 'vertex="1"' in attrs:
                shapes += 1

            if value:
                text_elements.append(value)

        return shapes, connections, text_elements

    def _build_drawio_xml(self, cells_xml, diagram_name="Converted Diagram"):
        """Wrap mxCell elements in a complete DrawIO file."""
        from datetime import datetime

        # Indent cells for clean formatting
        indented = "\n".join(
            f"        {line}" for line in cells_xml.strip().split("\n")
        )

        return DRAWIO_TEMPLATE.format(
            modified=datetime.now().isoformat(),
            name=diagram_name,
            cells=indented,
        )

    def _validate_xml(self, xml_content):
        """Basic XML validation."""
        try:
            ET.fromstring(xml_content)
            return True
        except ET.ParseError:
            return False

    def convert(self, image_path, diagram_name=None, extra_context=""):
        """
        Convert a Lucidchart screenshot to DrawIO XML.

        Args:
            image_path: Path to the screenshot PNG file
            diagram_name: Optional name for the diagram
            extra_context: Optional additional context about the diagram

        Returns:
            ConversionResult with the generated DrawIO XML and metadata
        """
        result = ConversionResult()

        if diagram_name is None:
            diagram_name = Path(image_path).stem

        try:
            # Load and encode image
            image_data = self._load_image_as_base64(image_path)
            media_type = self._get_media_type(image_path)

            # Build prompt with optional context
            prompt = VISION_PROMPT
            if extra_context:
                prompt += f"\n\n## Additional Context\n{extra_context}"

            # Call Claude Vision
            logger.info(f"Sending {image_path} to Claude Vision ({self.model})...")
            message = self.client.messages.create(
                model=self.model,
                max_tokens=8192,
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
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            )

            response_text = message.content[0].text
            result.raw_response = response_text

            # Extract XML from response
            cells_xml = self._extract_xml_from_response(response_text)

            if not cells_xml:
                result.error = "No mxCell XML found in response"
                return result

            # Count elements
            shapes, connections, text_elements = self._count_elements(cells_xml)
            result.shapes_detected = shapes
            result.connections_detected = connections
            result.text_elements = text_elements

            # Build complete DrawIO file
            drawio_xml = self._build_drawio_xml(cells_xml, diagram_name)

            # Validate XML
            if not self._validate_xml(drawio_xml):
                result.error = "Generated XML failed validation"
                result.confidence = 0.2
            else:
                result.drawio_xml = drawio_xml
                # Confidence based on content richness
                result.confidence = min(
                    1.0,
                    0.3  # base
                    + (0.2 if shapes >= 3 else 0.1 * shapes)
                    + (0.2 if connections >= 2 else 0.1 * connections)
                    + (0.3 if len(text_elements) >= 3 else 0.1 * len(text_elements)),
                )

            logger.info(
                f"Conversion complete: {shapes} shapes, {connections} connections, "
                f"confidence={result.confidence:.2f}"
            )

        except anthropic.APIError as e:
            result.error = f"API error: {e}"
            logger.error(result.error)
        except Exception as e:
            result.error = f"Conversion error: {e}"
            logger.error(result.error)

        return result

    def convert_and_save(self, image_path, output_path=None, diagram_name=None):
        """
        Convert a screenshot and save the result as a .drawio file.

        Args:
            image_path: Path to input screenshot
            output_path: Path for output .drawio file. Defaults to same dir as input.
            diagram_name: Optional diagram name

        Returns:
            ConversionResult
        """
        result = self.convert(image_path, diagram_name=diagram_name)

        if result.drawio_xml and not result.error:
            if output_path is None:
                output_path = Path(image_path).with_suffix(".drawio")

            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(result.drawio_xml)

            logger.info(f"Saved DrawIO file: {output_path}")

        return result
