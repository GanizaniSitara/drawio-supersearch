"""
DrawIO Converter: Screenshot → DrawIO XML

Uses Claude Vision API to analyze diagram screenshots and generate
editable DrawIO XML with high-fidelity shape, text, and connection recreation.
"""

import os
import json
import base64
import logging
import hashlib
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DRAWIO_CONVERSION_PROMPT = """You are an expert diagram analyst. Analyze this screenshot of a diagram and convert it into a valid DrawIO XML file.

IMPORTANT RULES:
1. Identify ALL shapes (rectangles, circles, diamonds, cylinders, clouds, hexagons, arrows, etc.)
2. Extract ALL text content exactly as shown - spelling, capitalization, and formatting must match
3. Map ALL connections between shapes - note the direction (arrows), line style (solid/dashed), and any labels on connections
4. Preserve the spatial layout as closely as possible - relative positions of shapes matter
5. Use appropriate DrawIO shape styles for each element type
6. Include proper colors where visible (background fills, border colors, text colors)
7. Group related elements together where logical

OUTPUT FORMAT:
Return ONLY valid DrawIO XML. The output must start with <?xml and be a complete .drawio file.
Use this structure:

<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net" modified="2024-01-01T00:00:00.000Z" agent="diagram-converter" version="1.0">
  <diagram id="converted" name="Page-1">
    <mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1169" pageHeight="827" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <!-- shapes and connections here -->
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>

SHAPE STYLE REFERENCE:
- Rectangle: style="rounded=0;whiteSpace=wrap;html=1;"
- Rounded Rectangle: style="rounded=1;whiteSpace=wrap;html=1;"
- Circle/Ellipse: style="ellipse;whiteSpace=wrap;html=1;"
- Diamond: style="rhombus;whiteSpace=wrap;html=1;"
- Cylinder (Database): style="shape=cylinder3;whiteSpace=wrap;html=1;size=15;"
- Cloud: style="ellipse;shape=cloud;whiteSpace=wrap;html=1;"
- Hexagon: style="shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;"
- Document: style="shape=document;whiteSpace=wrap;html=1;"
- Person/Actor: style="shape=mxgraph.basic.person;whiteSpace=wrap;html=1;"
- Arrow connection: style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;"

COLOR GUIDE:
- Use fillColor=#... for background colors
- Use strokeColor=#... for border colors
- Use fontColor=#... for text colors
- Common colors: #dae8fc (light blue), #d5e8d4 (light green), #ffe6cc (light orange), #f8cecc (light red), #e1d5e7 (light purple), #fff2cc (light yellow)

Be thorough and accurate. Every visible element in the screenshot should appear in the output XML."""


@dataclass
class ConversionResult:
    """Result of a diagram conversion attempt."""

    source_path: str
    drawio_xml: str = ""
    success: bool = False
    error: str = ""
    confidence_score: float = 0.0
    shape_count: int = 0
    connection_count: int = 0
    text_elements: list = field(default_factory=list)
    model_used: str = ""
    tokens_used: int = 0


class DrawIOConverter:
    """Converts diagram screenshots to DrawIO XML using Claude Vision."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514",
                 max_tokens: int = 8192, temperature: float = 0.0):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def _encode_image(self, image_path: str) -> tuple[str, str]:
        """Read and base64 encode an image file."""
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

    def _call_claude_api(self, image_data: str, media_type: str,
                         extra_context: str = "") -> dict:
        """Call the Anthropic API with a vision request."""
        import httpx

        prompt = DRAWIO_CONVERSION_PROMPT
        if extra_context:
            prompt += f"\n\nADDITIONAL CONTEXT about this diagram:\n{extra_context}"

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
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
                        {
                            "type": "text",
                            "text": prompt,
                        },
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
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json()

    def _extract_xml_from_response(self, response_text: str) -> str:
        """Extract DrawIO XML from the API response text."""
        # Try to find XML between markers
        if "<?xml" in response_text:
            start = response_text.index("<?xml")
            # Find the closing mxfile tag
            end_tag = "</mxfile>"
            if end_tag in response_text:
                end = response_text.index(end_tag) + len(end_tag)
                return response_text[start:end]
            # Fallback: take everything from <?xml onwards
            return response_text[start:]

        # Try code block extraction
        if "```xml" in response_text:
            start = response_text.index("```xml") + 6
            end = response_text.index("```", start)
            return response_text[start:end].strip()

        if "```" in response_text:
            start = response_text.index("```") + 3
            end = response_text.index("```", start)
            content = response_text[start:end].strip()
            if content.startswith("<?xml") or content.startswith("<mxfile"):
                return content

        return response_text.strip()

    def _validate_drawio_xml(self, xml_str: str) -> tuple[bool, str, dict]:
        """
        Validate that the XML is well-formed DrawIO.

        Returns: (is_valid, error_message, stats_dict)
        """
        import xml.etree.ElementTree as ET

        stats = {"shape_count": 0, "connection_count": 0, "text_elements": []}

        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError as e:
            return False, f"XML parse error: {e}", stats

        # Check for mxfile or mxGraphModel
        if root.tag not in ("mxfile", "mxGraphModel"):
            return False, f"Unexpected root element: {root.tag}", stats

        # Count shapes and connections
        for cell in root.iter("mxCell"):
            cell_id = cell.get("id", "")
            if cell_id in ("0", "1"):
                continue  # Skip root cells

            value = cell.get("value", "")
            style = cell.get("style", "")
            source = cell.get("source")
            target = cell.get("target")

            if source or target:
                stats["connection_count"] += 1
            elif style:  # Has a style = it's a visible shape
                stats["shape_count"] += 1

            if value:
                # Strip HTML
                import re
                clean = re.sub(r"<[^>]+>", " ", value).strip()
                if clean:
                    stats["text_elements"].append(clean)

        if stats["shape_count"] == 0 and stats["connection_count"] == 0:
            return False, "No shapes or connections found in XML", stats

        return True, "", stats

    def _compute_confidence(self, stats: dict, image_path: str) -> float:
        """
        Compute a confidence score for the conversion based on heuristics.

        Score 0.0-1.0 based on:
        - Number of shapes detected
        - Number of connections
        - Text content presence
        - Image file size (proxy for complexity)
        """
        score = 0.0

        # Shapes present (up to 0.3)
        shape_count = stats.get("shape_count", 0)
        if shape_count >= 10:
            score += 0.3
        elif shape_count >= 5:
            score += 0.25
        elif shape_count >= 2:
            score += 0.2
        elif shape_count >= 1:
            score += 0.1

        # Connections present (up to 0.3)
        conn_count = stats.get("connection_count", 0)
        if conn_count >= 5:
            score += 0.3
        elif conn_count >= 2:
            score += 0.2
        elif conn_count >= 1:
            score += 0.1

        # Text content (up to 0.2)
        text_count = len(stats.get("text_elements", []))
        if text_count >= 5:
            score += 0.2
        elif text_count >= 2:
            score += 0.15
        elif text_count >= 1:
            score += 0.1

        # Reasonable ratio of connections to shapes (up to 0.2)
        if shape_count > 0 and conn_count > 0:
            ratio = conn_count / shape_count
            if 0.3 <= ratio <= 3.0:
                score += 0.2
            elif 0.1 <= ratio <= 5.0:
                score += 0.1

        return min(score, 1.0)

    def convert(self, image_path: str, extra_context: str = "",
                output_path: Optional[str] = None) -> ConversionResult:
        """
        Convert a diagram screenshot to DrawIO XML.

        Args:
            image_path: Path to the screenshot image
            extra_context: Optional context about the diagram (page title, body text)
            output_path: Optional path to write the output XML

        Returns:
            ConversionResult with the conversion outcome
        """
        result = ConversionResult(source_path=image_path)

        if not os.path.exists(image_path):
            result.error = f"Image not found: {image_path}"
            return result

        try:
            # Encode image
            image_data, media_type = self._encode_image(image_path)

            # Call API
            logger.info(f"Converting: {os.path.basename(image_path)}")
            api_response = self._call_claude_api(image_data, media_type, extra_context)

            # Extract text content
            response_text = ""
            for block in api_response.get("content", []):
                if block.get("type") == "text":
                    response_text += block["text"]

            # Track usage
            usage = api_response.get("usage", {})
            result.tokens_used = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            result.model_used = self.model

            # Extract XML
            xml_str = self._extract_xml_from_response(response_text)

            # Validate
            is_valid, error, stats = self._validate_drawio_xml(xml_str)

            if not is_valid:
                result.error = error
                result.drawio_xml = xml_str  # Keep it even if invalid
                return result

            result.drawio_xml = xml_str
            result.success = True
            result.shape_count = stats["shape_count"]
            result.connection_count = stats["connection_count"]
            result.text_elements = stats["text_elements"]
            result.confidence_score = self._compute_confidence(stats, image_path)

            # Write output if path specified
            if output_path:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(xml_str)
                logger.info(f"  Written: {output_path} "
                            f"({result.shape_count} shapes, "
                            f"{result.connection_count} connections, "
                            f"confidence={result.confidence_score:.2f})")

        except Exception as e:
            result.error = str(e)
            logger.error(f"Conversion failed for {image_path}: {e}")

        return result
