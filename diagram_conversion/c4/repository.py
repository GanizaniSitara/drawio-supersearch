"""
C4 Repository: Aggregates C4 models into a navigable architecture repository.

Provides:
- Cross-model system index (all systems across all diagrams)
- Technology inventory
- Relationship graph (which systems connect to which)
- Drill-down navigation: Context → Container → Component
- Summary statistics
"""

import os
import json
import logging
from collections import defaultdict
from typing import Optional

from ..pipeline.database import ConversionDB

logger = logging.getLogger(__name__)


class C4Repository:
    """Aggregated view of all C4 architecture models."""

    def __init__(self, db: ConversionDB):
        self.db = db

    def get_system_index(self) -> list[dict]:
        """
        Get a deduplicated index of all systems across all C4 models.
        Systems with the same name are merged.
        """
        conn = self.db._connect()
        rows = conn.execute(
            "SELECT s.*, m.title as model_title, m.c4_level, m.id as model_id, "
            "c.source_name, c.space_key "
            "FROM c4_systems s "
            "JOIN c4_models m ON s.model_id = m.id "
            "JOIN conversions c ON m.conversion_id = c.id "
            "ORDER BY s.name"
        ).fetchall()
        conn.close()

        # Merge systems by name
        merged = {}
        for row in rows:
            row = dict(row)
            name = row["name"].strip()
            name_lower = name.lower()

            if name_lower not in merged:
                merged[name_lower] = {
                    "name": name,
                    "type": row["type"],
                    "description": row["description"],
                    "technology": row["technology"],
                    "is_external": bool(row["is_external"]),
                    "tags": json.loads(row.get("tags", "[]")),
                    "appears_in": [],
                }

            merged[name_lower]["appears_in"].append({
                "model_id": row["model_id"],
                "model_title": row["model_title"],
                "c4_level": row["c4_level"],
                "source_name": row["source_name"],
                "space_key": row["space_key"],
            })

            # Merge technology info
            if row["technology"] and not merged[name_lower]["technology"]:
                merged[name_lower]["technology"] = row["technology"]
            if row["description"] and not merged[name_lower]["description"]:
                merged[name_lower]["description"] = row["description"]

        return sorted(merged.values(), key=lambda x: x["name"])

    def get_technology_inventory(self) -> dict:
        """
        Get a summary of all technologies found across C4 models.
        Returns: {technology: [list of systems using it]}
        """
        conn = self.db._connect()
        rows = conn.execute(
            "SELECT DISTINCT s.technology, s.name, s.type "
            "FROM c4_systems s WHERE s.technology != '' "
            "ORDER BY s.technology, s.name"
        ).fetchall()

        # Also get relationship technologies
        rel_rows = conn.execute(
            "SELECT DISTINCT technology FROM c4_relationships "
            "WHERE technology != ''"
        ).fetchall()
        conn.close()

        inventory = defaultdict(list)
        for row in rows:
            inventory[row["technology"]].append({
                "name": row["name"],
                "type": row["type"],
            })

        # Add relationship technologies
        for row in rel_rows:
            if row["technology"] not in inventory:
                inventory[row["technology"]] = []

        return dict(sorted(inventory.items()))

    def get_relationship_graph(self) -> dict:
        """
        Build a graph of all system relationships across models.
        Returns: {nodes: [...], edges: [...]}
        """
        conn = self.db._connect()

        systems = conn.execute(
            "SELECT DISTINCT s.system_id, s.name, s.type, s.technology, s.is_external "
            "FROM c4_systems s"
        ).fetchall()

        relationships = conn.execute(
            "SELECT r.source_system_id, r.target_system_id, "
            "r.description, r.technology, "
            "s1.name as source_name, s2.name as target_name "
            "FROM c4_relationships r "
            "LEFT JOIN c4_systems s1 ON r.source_system_id = s1.system_id "
            "AND r.model_id = s1.model_id "
            "LEFT JOIN c4_systems s2 ON r.target_system_id = s2.system_id "
            "AND r.model_id = s2.model_id"
        ).fetchall()
        conn.close()

        # Deduplicate nodes by name
        node_map = {}
        for s in systems:
            name = s["name"].strip().lower()
            if name not in node_map:
                node_map[name] = {
                    "id": name,
                    "name": s["name"],
                    "type": s["type"],
                    "technology": s["technology"] or "",
                    "is_external": bool(s["is_external"]),
                }

        # Deduplicate edges
        edge_set = set()
        edges = []
        for r in relationships:
            source = (r["source_name"] or "").strip().lower()
            target = (r["target_name"] or "").strip().lower()
            if not source or not target:
                continue
            edge_key = (source, target)
            if edge_key not in edge_set:
                edge_set.add(edge_key)
                edges.append({
                    "source": source,
                    "target": target,
                    "description": r["description"] or "",
                    "technology": r["technology"] or "",
                })

        return {
            "nodes": list(node_map.values()),
            "edges": edges,
        }

    def get_summary_stats(self) -> dict:
        """Generate summary statistics for the C4 repository."""
        conn = self.db._connect()

        total_models = conn.execute("SELECT COUNT(*) FROM c4_models").fetchone()[0]
        by_level = conn.execute(
            "SELECT c4_level, COUNT(*) as cnt FROM c4_models GROUP BY c4_level"
        ).fetchall()

        total_systems = conn.execute(
            "SELECT COUNT(DISTINCT name) FROM c4_systems"
        ).fetchone()[0]
        total_relationships = conn.execute(
            "SELECT COUNT(*) FROM c4_relationships"
        ).fetchone()[0]

        by_type = conn.execute(
            "SELECT type, COUNT(DISTINCT name) as cnt FROM c4_systems GROUP BY type"
        ).fetchall()

        external_count = conn.execute(
            "SELECT COUNT(DISTINCT name) FROM c4_systems WHERE is_external = 1"
        ).fetchone()[0]

        tech_count = conn.execute(
            "SELECT COUNT(DISTINCT technology) FROM c4_systems WHERE technology != ''"
        ).fetchone()[0]

        conn.close()

        return {
            "total_models": total_models,
            "by_level": {r["c4_level"]: r["cnt"] for r in by_level},
            "total_unique_systems": total_systems,
            "total_relationships": total_relationships,
            "systems_by_type": {r["type"]: r["cnt"] for r in by_type},
            "external_systems": external_count,
            "internal_systems": total_systems - external_count,
            "unique_technologies": tech_count,
        }

    def find_system(self, name: str) -> Optional[dict]:
        """
        Find a system by name and return all models it appears in.
        """
        conn = self.db._connect()
        rows = conn.execute(
            "SELECT s.*, m.id as model_id, m.title as model_title, m.c4_level, "
            "m.model_json, c.source_name, c.space_key, c.source_path "
            "FROM c4_systems s "
            "JOIN c4_models m ON s.model_id = m.id "
            "JOIN conversions c ON m.conversion_id = c.id "
            "WHERE s.name LIKE ? "
            "ORDER BY m.c4_level",
            (f"%{name}%",),
        ).fetchall()
        conn.close()

        if not rows:
            return None

        first = dict(rows[0])
        result = {
            "name": first["name"],
            "type": first["type"],
            "description": first["description"],
            "technology": first["technology"],
            "is_external": bool(first["is_external"]),
            "models": [],
        }

        for row in rows:
            row = dict(row)
            result["models"].append({
                "model_id": row["model_id"],
                "model_title": row["model_title"],
                "c4_level": row["c4_level"],
                "source_name": row["source_name"],
                "space_key": row["space_key"],
            })

        return result
