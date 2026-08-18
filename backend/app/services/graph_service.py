"""Memgraph-backed Knowledge Graph service for GraphRAG.

Handles entity/relationship extraction from documents using an LLM,
stores them as a graph in Memgraph via Bolt protocol, and retrieves
relevant subgraphs for query-time context enrichment.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from neo4j import GraphDatabase

from backend.app.config import get_settings

logger = logging.getLogger(__name__)

# ── Extraction prompt (used with Gemini) ────────────────────────────────

EXTRACTION_PROMPT = """You are an expert knowledge-graph builder.
Given the following text chunk from a document, extract ALL meaningful entities and relationships.

TEXT:
{text}

RULES:
1. Extract entities as (label, name) pairs. Labels should be general categories like Person, Organization, Project, Technology, Date, Location, Concept, Event, etc.
2. Extract relationships as (source_name, relationship_type, target_name) triples.
3. Relationship types should be UPPER_SNAKE_CASE (e.g., WORKS_AT, CREATED_BY, USES, HAS_DEADLINE).
4. Be thorough — extract every factual relationship you can find.
5. Normalize entity names: use title case, remove extra whitespace.

Respond ONLY with valid JSON in this exact format:
{{
  "entities": [
    {{"label": "Person", "name": "Harsh Sharma"}},
    {{"label": "Project", "name": "AI Orchestrator"}}
  ],
  "relationships": [
    {{"source": "Harsh Sharma", "relationship": "BUILT", "target": "AI Orchestrator"}}
  ]
}}

If no entities or relationships can be extracted, return:
{{"entities": [], "relationships": []}}
"""


class MemgraphService:
    """Production-grade Memgraph knowledge-graph service.

    - Uses the official Neo4j Python driver (Bolt protocol).
    - Gracefully degrades if Memgraph is unavailable (returns empty results).
    - Extracts entities/relationships from text chunks using Gemini LLM.
    """

    def __init__(self, uri: Optional[str] = None) -> None:
        settings = get_settings()
        self._uri = uri or settings.MEMGRAPH_URI
        self._driver = None
        self._available = False

        try:
            self._driver = GraphDatabase.driver(self._uri, auth=("", ""))
            # Quick connectivity check
            with self._driver.session() as session:
                session.run("RETURN 1")
            self._available = True
            logger.info("Memgraph connected at %s", self._uri)
        except Exception as exc:
            logger.warning(
                "Memgraph unavailable at %s: %s. GraphRAG will operate in FAISS-only mode.",
                self._uri, exc,
            )
            self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    def close(self) -> None:
        if self._driver:
            self._driver.close()

    # ── Schema setup ────────────────────────────────────────────────────

    def ensure_indexes(self) -> None:
        """Create indexes for fast entity lookups."""
        if not self._available:
            return
        try:
            with self._driver.session() as session:
                session.run("CREATE INDEX ON :Entity(name);")
                session.run("CREATE INDEX ON :Entity(label);")
                session.run("CREATE INDEX ON :Entity(source_doc);")
            logger.info("Memgraph indexes created/ensured.")
        except Exception as exc:
            # Indexes may already exist
            logger.debug("Index creation note: %s", exc)

    # ── Entity/Relationship extraction via LLM ──────────────────────────

    async def extract_graph_from_chunks(
        self,
        chunks: List[str],
        source_filename: str,
        llm_router: Any,
    ) -> Tuple[int, int]:
        """Extract entities and relationships from text chunks and store in Memgraph.

        Returns (entities_count, relationships_count).
        """
        if not self._available:
            logger.info("Memgraph unavailable, skipping graph extraction.")
            return 0, 0

        from langchain_core.prompts import ChatPromptTemplate

        total_entities = 0
        total_rels = 0

        for i, chunk in enumerate(chunks):
            try:
                prompt = ChatPromptTemplate.from_messages([
                    ("system", "You are a knowledge graph extraction expert. Always respond with valid JSON only."),
                    ("human", EXTRACTION_PROMPT.format(text=chunk[:2000])),  # Cap chunk size for LLM
                ])
                msgs = prompt.format_messages()
                resp = await llm_router.ainvoke(msgs, temperature=0.0)
                raw_text = getattr(resp, "content", str(resp))

                # Parse JSON from response
                extracted = self._parse_extraction_response(raw_text)
                if not extracted:
                    continue

                entities = extracted.get("entities", [])
                relationships = extracted.get("relationships", [])

                # Store in Memgraph
                await asyncio.to_thread(
                    self._store_graph_data,
                    entities,
                    relationships,
                    source_filename,
                )

                total_entities += len(entities)
                total_rels += len(relationships)

                logger.debug(
                    "Chunk %d/%d: extracted %d entities, %d relationships",
                    i + 1, len(chunks), len(entities), len(relationships),
                )

            except Exception as exc:
                logger.warning("Graph extraction failed for chunk %d: %s", i, exc)
                continue

        logger.info(
            "Graph extraction complete for %s: %d entities, %d relationships",
            source_filename, total_entities, total_rels,
        )
        return total_entities, total_rels

    def _parse_extraction_response(self, raw: str) -> Optional[Dict]:
        """Robustly parse JSON from LLM response, handling markdown code blocks."""
        import json

        # Strip markdown code blocks if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # Remove ```json or ``` prefix/suffix
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to find JSON object in the text
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning("Failed to parse extraction JSON: %s", raw[:200])
            return None

    def _store_graph_data(
        self,
        entities: List[Dict],
        relationships: List[Dict],
        source_doc: str,
    ) -> None:
        """Write entities and relationships to Memgraph using Cypher."""
        if not self._available or not self._driver:
            return

        with self._driver.session() as session:
            # Merge entities (upsert — won't duplicate)
            for entity in entities:
                name = entity.get("name", "").strip()
                label = entity.get("label", "Entity").strip()
                if not name:
                    continue
                cypher = (
                    "MERGE (e:Entity {name: $name}) "
                    "SET e.label = $label, e.source_doc = $source_doc"
                )
                session.run(cypher, name=name, label=label, source_doc=source_doc)

            # Merge relationships
            for rel in relationships:
                source = rel.get("source", "").strip()
                target = rel.get("target", "").strip()
                rel_type = rel.get("relationship", "RELATED_TO").strip()
                if not source or not target:
                    continue
                # Sanitize relationship type for Cypher
                rel_type = re.sub(r"[^A-Z0-9_]", "_", rel_type.upper())
                cypher = (
                    f"MERGE (a:Entity {{name: $source}}) "
                    f"MERGE (b:Entity {{name: $target}}) "
                    f"MERGE (a)-[r:{rel_type}]->(b) "
                    f"SET r.source_doc = $source_doc"
                )
                session.run(cypher, source=source, target=target, source_doc=source_doc)

    # ── Query-time graph retrieval ──────────────────────────────────────

    async def query_graph(
        self,
        user_query: str,
        llm_router: Any,
        max_hops: int = 2,
        limit: int = 20,
    ) -> str:
        """Extract key entities from the query, then retrieve their neighborhood from Memgraph.

        Returns a formatted string of graph context for the LLM.
        """
        if not self._available:
            return ""

        # Step 1: Extract key entities from the user's question using LLM
        entities = await self._extract_query_entities(user_query, llm_router)
        if not entities:
            # Fallback: try keyword matching against the graph
            entities = await asyncio.to_thread(self._keyword_entity_search, user_query)

        if not entities:
            logger.info("No graph entities found for query: %s", user_query[:80])
            return ""

        # Step 2: Retrieve subgraph around those entities
        subgraph = await asyncio.to_thread(
            self._get_entity_neighborhood,
            entities,
            max_hops,
            limit,
        )

        if not subgraph:
            return ""

        # Step 3: Format as context string
        context = self._format_graph_context(subgraph)
        logger.info("Graph context assembled: %d chars for %d entities", len(context), len(entities))
        return context

    async def _extract_query_entities(self, query: str, llm_router: Any) -> List[str]:
        """Use LLM to identify entity names mentioned in the user's question."""
        from langchain_core.prompts import ChatPromptTemplate

        prompt = ChatPromptTemplate.from_messages([
            ("system", "You extract entity names from questions. Return ONLY a JSON array of entity name strings. No explanation."),
            ("human", "Extract all entity names (people, projects, organizations, technologies, etc.) from this question:\n\n\"{query}\"\n\nReturn JSON array like: [\"Entity1\", \"Entity2\"]"),
        ])

        try:
            msgs = prompt.format_messages(query=query)
            resp = await llm_router.ainvoke(msgs, temperature=0.0)
            raw = getattr(resp, "content", str(resp)).strip()

            import json
            # Clean markdown
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)
            entities = json.loads(cleaned)
            if isinstance(entities, list):
                return [str(e).strip() for e in entities if e]
        except Exception as exc:
            logger.debug("Entity extraction from query failed: %s", exc)

        return []

    def _keyword_entity_search(self, query: str, limit: int = 10) -> List[str]:
        """Fallback: search entities in Memgraph whose names contain query keywords."""
        if not self._available or not self._driver:
            return []

        words = [w for w in query.split() if len(w) > 2]
        found = []

        with self._driver.session() as session:
            for word in words[:5]:  # Limit keyword search
                result = session.run(
                    "MATCH (e:Entity) WHERE toLower(e.name) CONTAINS toLower($word) "
                    "RETURN e.name AS name LIMIT $limit",
                    word=word, limit=limit,
                )
                for record in result:
                    name = record["name"]
                    if name not in found:
                        found.append(name)

        return found[:limit]

    def _get_entity_neighborhood(
        self,
        entities: List[str],
        max_hops: int = 2,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Retrieve relationships within N hops of the given entities."""
        if not self._available or not self._driver:
            return []

        results = []
        with self._driver.session() as session:
            for entity_name in entities[:10]:  # Cap to avoid excessive queries
                cypher = (
                    "MATCH (a:Entity {name: $name})-[r*1.." + str(max_hops) + "]-(b:Entity) "
                    "UNWIND r AS rel "
                    "WITH startNode(rel) AS src, rel, endNode(rel) AS tgt "
                    "RETURN src.name AS source, src.label AS source_label, "
                    "type(rel) AS relationship, "
                    "tgt.name AS target, tgt.label AS target_label "
                    "LIMIT $limit"
                )
                records = session.run(cypher, name=entity_name, limit=limit)
                for record in records:
                    triple = {
                        "source": record["source"],
                        "source_label": record.get("source_label", ""),
                        "relationship": record["relationship"],
                        "target": record["target"],
                        "target_label": record.get("target_label", ""),
                    }
                    # Avoid duplicates
                    if triple not in results:
                        results.append(triple)

        return results[:limit]

    def _format_graph_context(self, subgraph: List[Dict[str, Any]]) -> str:
        """Format graph triples into a human-readable context string for the LLM."""
        if not subgraph:
            return ""

        lines = ["=== Knowledge Graph Context ==="]
        for triple in subgraph:
            src = triple["source"]
            rel = triple["relationship"].replace("_", " ").title()
            tgt = triple["target"]
            src_label = f" ({triple['source_label']})" if triple.get("source_label") else ""
            tgt_label = f" ({triple['target_label']})" if triple.get("target_label") else ""
            lines.append(f"• {src}{src_label} → {rel} → {tgt}{tgt_label}")

        lines.append("=== End Knowledge Graph Context ===")
        return "\n".join(lines)

    # ── Stats ───────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, int]:
        """Return node and relationship counts."""
        if not self._available or not self._driver:
            return {"nodes": 0, "relationships": 0, "available": False}

        with self._driver.session() as session:
            nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

        return {"nodes": nodes, "relationships": rels, "available": True}
