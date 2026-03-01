"""Tests for the C4 repository aggregation."""

import os
import json
import tempfile
import pytest

from diagram_conversion.pipeline.database import ConversionDB
from diagram_conversion.c4.repository import C4Repository


class TestC4Repository:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = ConversionDB(os.path.join(self.tmpdir, "test.db"))
        self.repo = C4Repository(self.db)

        # Seed test data
        id1 = self.db.upsert_conversion(
            "/test/order_system.png",
            source_name="order_system",
            space_key="OPS",
        )
        id2 = self.db.upsert_conversion(
            "/test/payment_system.png",
            source_name="payment_system",
            space_key="PAY",
        )

        self.db.save_c4_model(id1, {
            "c4_level": "context",
            "title": "Order Processing",
            "description": "Order processing system context",
            "systems": [
                {"id": "order_svc", "name": "Order Service", "type": "system",
                 "description": "Handles orders", "technology": "Java/Spring",
                 "is_external": False, "tags": ["core"]},
                {"id": "payment_gw", "name": "Payment Gateway", "type": "external_system",
                 "description": "Processes payments", "technology": "Stripe",
                 "is_external": True, "tags": ["external"]},
                {"id": "customer", "name": "Customer", "type": "person",
                 "description": "End user", "technology": "",
                 "is_external": False, "tags": []},
            ],
            "relationships": [
                {"source_id": "customer", "target_id": "order_svc",
                 "description": "places orders", "technology": "HTTPS"},
                {"source_id": "order_svc", "target_id": "payment_gw",
                 "description": "processes payments", "technology": "REST"},
            ],
            "boundaries": [],
            "metadata": {},
        })

        self.db.save_c4_model(id2, {
            "c4_level": "container",
            "title": "Payment System",
            "description": "Payment system containers",
            "systems": [
                {"id": "pay_api", "name": "Payment API", "type": "container",
                 "description": "Payment REST API", "technology": "Node.js",
                 "is_external": False, "tags": []},
                {"id": "pay_db", "name": "Payment Database", "type": "database",
                 "description": "Payment records", "technology": "PostgreSQL",
                 "is_external": False, "tags": []},
                {"id": "order_svc", "name": "Order Service", "type": "system",
                 "description": "External order system", "technology": "Java/Spring",
                 "is_external": True, "tags": []},
            ],
            "relationships": [
                {"source_id": "order_svc", "target_id": "pay_api",
                 "description": "sends payment requests", "technology": "REST"},
                {"source_id": "pay_api", "target_id": "pay_db",
                 "description": "stores records", "technology": "SQL"},
            ],
            "boundaries": [],
            "metadata": {},
        })

    def test_system_index(self):
        systems = self.repo.get_system_index()
        names = [s["name"] for s in systems]

        assert "Order Service" in names
        assert "Payment Gateway" in names
        assert "Customer" in names

        # Order Service should appear in both models
        order_svc = next(s for s in systems if s["name"] == "Order Service")
        assert len(order_svc["appears_in"]) == 2

    def test_technology_inventory(self):
        inventory = self.repo.get_technology_inventory()

        assert "Java/Spring" in inventory
        assert "PostgreSQL" in inventory
        assert "Node.js" in inventory

    def test_relationship_graph(self):
        graph = self.repo.get_relationship_graph()

        assert len(graph["nodes"]) > 0
        assert len(graph["edges"]) > 0

        node_names = {n["name"].lower() for n in graph["nodes"]}
        assert "order service" in node_names

    def test_summary_stats(self):
        stats = self.repo.get_summary_stats()

        assert stats["total_models"] == 2
        assert "context" in stats["by_level"]
        assert "container" in stats["by_level"]
        assert stats["total_unique_systems"] > 0
        assert stats["total_relationships"] > 0

    def test_find_system(self):
        result = self.repo.find_system("Order Service")
        assert result is not None
        assert result["name"] == "Order Service"
        assert len(result["models"]) >= 1

    def test_find_system_not_found(self):
        result = self.repo.find_system("Nonexistent System XYZ")
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
