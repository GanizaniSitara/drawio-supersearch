"""Tests for the diagram conversion modules."""

import os
import json
import tempfile
import pytest

from diagram_conversion.converters.drawio_converter import DrawIOConverter, ConversionResult
from diagram_conversion.converters.classifier import DiagramClassifier, ClassificationResult, DIAGRAM_TYPES
from diagram_conversion.converters.c4_converter import C4Converter, C4Model, C4ConversionResult
from diagram_conversion.pipeline.database import ConversionDB
from diagram_conversion.config import ConversionConfig


# ── Config Tests ────────────────────────────────────────────────────

class TestConfig:
    def test_default_config(self):
        config = ConversionConfig()
        assert config.model == "claude-sonnet-4-20250514"
        assert config.batch_size == 10
        assert config.min_confidence_score == 0.5

    def test_directory_derivation(self):
        config = ConversionConfig(output_dir="/tmp/test_output")
        assert config.drawio_output_dir == "/tmp/test_output/drawio"
        assert config.c4_output_dir == "/tmp/test_output/c4"

    def test_ensure_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = ConversionConfig(output_dir=os.path.join(tmpdir, "out"),
                                       db_path=os.path.join(tmpdir, "db", "test.db"))
            config.ensure_directories()
            assert os.path.exists(config.drawio_output_dir)
            assert os.path.exists(config.c4_output_dir)


# ── DrawIO Converter Tests ──────────────────────────────────────────

class TestDrawIOConverter:
    def test_validate_valid_xml(self):
        converter = DrawIOConverter(api_key="test", model="test")
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <mxfile>
          <diagram id="test" name="Page-1">
            <mxGraphModel>
              <root>
                <mxCell id="0" />
                <mxCell id="1" parent="0" />
                <mxCell id="2" value="Server" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
                  <mxGeometry x="100" y="100" width="120" height="60" as="geometry" />
                </mxCell>
                <mxCell id="3" value="Database" style="shape=cylinder3;" vertex="1" parent="1">
                  <mxGeometry x="300" y="100" width="120" height="60" as="geometry" />
                </mxCell>
                <mxCell id="4" value="connects" style="edgeStyle=orthogonalEdgeStyle;" edge="1" source="2" target="3" parent="1" />
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>"""

        is_valid, error, stats = converter._validate_drawio_xml(xml)
        assert is_valid
        assert stats["shape_count"] == 2
        assert stats["connection_count"] == 1
        assert "Server" in stats["text_elements"]
        assert "Database" in stats["text_elements"]

    def test_validate_empty_xml(self):
        converter = DrawIOConverter(api_key="test", model="test")
        xml = """<?xml version="1.0"?>
        <mxfile><diagram><mxGraphModel><root>
            <mxCell id="0" /><mxCell id="1" parent="0" />
        </root></mxGraphModel></diagram></mxfile>"""

        is_valid, error, stats = converter._validate_drawio_xml(xml)
        assert not is_valid
        assert "No shapes" in error

    def test_validate_malformed_xml(self):
        converter = DrawIOConverter(api_key="test", model="test")
        is_valid, error, _ = converter._validate_drawio_xml("<not>valid<xml")
        assert not is_valid

    def test_extract_xml_from_response(self):
        converter = DrawIOConverter(api_key="test", model="test")

        # XML directly in response
        response = 'Some text\n<?xml version="1.0"?>\n<mxfile></mxfile>\nMore text'
        xml = converter._extract_xml_from_response(response)
        assert xml.startswith("<?xml")
        assert xml.endswith("</mxfile>")

        # XML in code block
        response = '```xml\n<?xml version="1.0"?>\n<mxfile></mxfile>\n```'
        xml = converter._extract_xml_from_response(response)
        assert "mxfile" in xml

    def test_confidence_scoring(self):
        converter = DrawIOConverter(api_key="test", model="test")

        # Good diagram with shapes, connections, text
        stats = {"shape_count": 10, "connection_count": 8,
                 "text_elements": ["A", "B", "C", "D", "E"]}
        score = converter._compute_confidence(stats, "test.png")
        assert score >= 0.8

        # Minimal diagram
        stats = {"shape_count": 1, "connection_count": 0, "text_elements": []}
        score = converter._compute_confidence(stats, "test.png")
        assert score <= 0.3

    def test_convert_missing_file(self):
        converter = DrawIOConverter(api_key="test", model="test")
        result = converter.convert("/nonexistent/path.png")
        assert not result.success
        assert "not found" in result.error


# ── Classifier Tests ────────────────────────────────────────────────

class TestDiagramClassifier:
    def test_text_classification_network(self):
        classifier = DiagramClassifier(api_key="test")
        result = classifier.classify_from_text(
            "Network Topology Diagram",
            "Infrastructure - Firewall and Switch Layout"
        )
        assert result.diagram_type == "network"
        assert result.confidence > 0
        assert result.is_system_diagram

    def test_text_classification_application(self):
        classifier = DiagramClassifier(api_key="test")
        result = classifier.classify_from_text(
            "Microservice Architecture",
            "Application deployment on Kubernetes"
        )
        assert result.diagram_type == "application"
        assert result.c4_convertible

    def test_text_classification_process(self):
        classifier = DiagramClassifier(api_key="test")
        result = classifier.classify_from_text(
            "Approval Workflow",
            "Business process for invoice approval"
        )
        assert result.diagram_type == "process"

    def test_text_classification_unknown(self):
        classifier = DiagramClassifier(api_key="test")
        result = classifier.classify_from_text(
            "Random Diagram",
            "Just some random content here"
        )
        assert result.diagram_type == "other"
        assert result.confidence <= 0.3

    def test_all_diagram_types_valid(self):
        assert "network" in DIAGRAM_TYPES
        assert "application" in DIAGRAM_TYPES
        assert "other" in DIAGRAM_TYPES


# ── C4 Model Tests ──────────────────────────────────────────────────

class TestC4Model:
    def test_model_creation(self):
        model = C4Model(
            c4_level="context",
            title="Order System",
            systems=[
                {"id": "s1", "name": "Order Service", "type": "system"},
                {"id": "s2", "name": "Payment Gateway", "type": "external_system"},
            ],
            relationships=[
                {"source_id": "s1", "target_id": "s2", "description": "processes payments"},
            ],
        )
        assert model.c4_level == "context"
        assert len(model.systems) == 2
        assert len(model.relationships) == 1

    def test_model_serialization(self):
        model = C4Model(
            c4_level="container",
            title="Test",
            systems=[{"id": "s1", "name": "API", "type": "container"}],
        )
        d = model.to_dict()
        assert d["c4_level"] == "container"

        json_str = model.to_json()
        parsed = json.loads(json_str)
        assert parsed["title"] == "Test"

    def test_model_from_dict(self):
        data = {
            "c4_level": "component",
            "title": "Auth Module",
            "systems": [{"id": "c1", "name": "AuthController", "type": "component"}],
            "relationships": [],
            "boundaries": [],
            "metadata": {"confidence": 0.9},
        }
        model = C4Model.from_dict(data)
        assert model.title == "Auth Module"
        assert len(model.systems) == 1

    def test_get_system_names(self):
        model = C4Model(systems=[
            {"name": "API Gateway"},
            {"name": "Database"},
        ])
        names = model.get_system_names()
        assert "API Gateway" in names
        assert "Database" in names

    def test_get_technologies(self):
        model = C4Model(
            systems=[
                {"name": "API", "technology": "Java/Spring"},
                {"name": "DB", "technology": "PostgreSQL"},
            ],
            relationships=[
                {"source_id": "a", "target_id": "b", "technology": "REST/HTTPS"},
            ],
        )
        techs = model.get_technologies()
        assert "Java/Spring" in techs
        assert "PostgreSQL" in techs
        assert "REST/HTTPS" in techs

    def test_to_drawio_c4(self):
        model = C4Model(
            c4_level="context",
            title="Test System",
            systems=[
                {"id": "s1", "name": "Web App", "type": "system",
                 "description": "Frontend", "technology": "React",
                 "is_external": False},
                {"id": "s2", "name": "Payment", "type": "external_system",
                 "description": "External payment", "technology": "Stripe",
                 "is_external": True},
            ],
            relationships=[
                {"source_id": "s1", "target_id": "s2",
                 "description": "sends payments", "technology": "HTTPS"},
            ],
        )
        xml = model.to_drawio_c4()
        assert "<?xml" in xml
        assert "mxfile" in xml
        assert "Web App" in xml
        assert "Payment" in xml


# ── Database Tests ──────────────────────────────────────────────────

class TestConversionDB:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = ConversionDB(os.path.join(self.tmpdir, "test.db"))

    def test_upsert_and_get(self):
        record_id = self.db.upsert_conversion(
            "/test/image.png",
            source_name="image",
            space_key="SPACE1",
        )
        assert record_id > 0

        record = self.db.get_conversion("/test/image.png")
        assert record is not None
        assert record["source_name"] == "image"
        assert record["space_key"] == "SPACE1"

    def test_upsert_update(self):
        self.db.upsert_conversion("/test/img.png", source_name="img")
        self.db.upsert_conversion("/test/img.png", diagram_type="network")

        record = self.db.get_conversion("/test/img.png")
        assert record["diagram_type"] == "network"
        assert record["source_name"] == "img"

    def test_pending_conversions(self):
        self.db.upsert_conversion("/test/a.png", source_name="a")
        self.db.upsert_conversion("/test/b.png", source_name="b")

        pending = self.db.get_pending_conversions()
        assert len(pending) == 2

    def test_c4_candidates(self):
        self.db.upsert_conversion(
            "/test/sys.png",
            source_name="sys",
            c4_convertible=1,
            drawio_status="success",
            drawio_confidence=0.9,
        )

        candidates = self.db.get_c4_candidates()
        assert len(candidates) == 1

    def test_stats(self):
        self.db.upsert_conversion("/test/a.png", source_name="a",
                                   drawio_status="success", diagram_type="network")
        self.db.upsert_conversion("/test/b.png", source_name="b",
                                   drawio_status="pending", diagram_type="application")

        stats = self.db.get_stats()
        assert stats["total_conversions"] == 2
        assert stats["by_status"]["success"] == 1
        assert stats["by_status"]["pending"] == 1

    def test_search(self):
        self.db.upsert_conversion("/test/network_topology.png",
                                   source_name="network_topology",
                                   page_title="Network Diagram")
        results = self.db.search_conversions("network")
        assert len(results) >= 1

    def test_pipeline_run(self):
        run_id = self.db.start_pipeline_run("test", 10)
        assert run_id > 0

        self.db.update_pipeline_run(run_id, processed=5, succeeded=4, failed=1)
        self.db.complete_pipeline_run(run_id, total_tokens=1000)

    def test_save_c4_model(self):
        conv_id = self.db.upsert_conversion("/test/sys.png", source_name="sys")

        model_dict = {
            "c4_level": "context",
            "title": "Test System",
            "description": "A test system",
            "systems": [
                {"id": "s1", "name": "App", "type": "system",
                 "description": "Main app", "technology": "Java",
                 "is_external": False, "tags": ["core"]},
            ],
            "relationships": [
                {"source_id": "s1", "target_id": "s2",
                 "description": "calls", "technology": "REST"},
            ],
            "boundaries": [],
            "metadata": {},
        }

        model_id = self.db.save_c4_model(conv_id, model_dict)
        assert model_id > 0

        models = self.db.get_all_c4_models()
        assert len(models) >= 1

        model = self.db.get_c4_model(model_id)
        assert model is not None
        assert model["title"] == "Test System"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
