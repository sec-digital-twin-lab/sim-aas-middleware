import os

from pydantic import BaseModel
from typing import Optional, List
import requests
from rdflib import ConjunctiveGraph, Graph


class SPARQLWrapper:
    class Config(BaseModel):
        endpoint: str
        update_endpoint: Optional[str] = None
        username: Optional[str] = None
        password: Optional[str] = None
        default_graph: Optional[str] = None
        timeout: int = 30

    class Event(BaseModel):
        type: str
        sparql: str
        result: Optional[dict] = None

    def __init__(self, config: Config, keep_history: bool = False):
        self._config = config
        self._session = requests.Session()
        self._history: Optional[List[SPARQLWrapper.Event]] = [] if keep_history else None

        if config.username and config.password:
            self._session.auth = (config.username, config.password)

    # ---------------------
    # Context manager
    # ---------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---------------------
    # Query / Update
    # ---------------------
    def query(self, sparql: str) -> dict:
        """Execute a SPARQL SELECT or ASK query (read operation)."""
        params = {"query": sparql}
        if self._config.default_graph:
            params["default-graph-uri"] = self._config.default_graph

        headers = {"Accept": "application/sparql-results+json"}
        resp = self._session.get(
            self._config.endpoint,
            params=params,
            headers=headers,
            timeout=self._config.timeout,
        )
        resp.raise_for_status()
        result = resp.json()

        if self._history is not None:
            self._history.append(SPARQLWrapper.Event(type="query", sparql=sparql, result=result))

        return result

    def update(self, sparql: str) -> None:
        """Execute a SPARQL INSERT, DELETE, or other update operation."""
        endpoint = self._config.update_endpoint or self._config.endpoint
        headers = {"Content-Type": "application/sparql-update"}
        resp = self._session.post(
            endpoint,
            data=sparql.encode("utf-8"),
            headers=headers,
            timeout=self._config.timeout,
        )
        resp.raise_for_status()

        if self._history is not None:
            self._history.append(SPARQLWrapper.Event(type="update", sparql=sparql, result=None))

    # ---------------------
    # Execute (auto-detect type)
    # ---------------------
    def execute(self, sparql: str) -> Optional[dict]:
        op = sparql.strip().split(maxsplit=1)[0].upper()
        if op in ("SELECT", "ASK", "CONSTRUCT", "DESCRIBE"):
            return self.query(sparql)
        else:
            self.update(sparql)
            return None

    # ---------------------
    # Portable RDF import
    # ---------------------
    def import_ttl(self, file_path: str, batch_size: int = 100) -> None:
        """Import a Turtle RDF file by converting it to SPARQL INSERT DATA statements."""
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Turtle file '{file_path}' not found")

        g = Graph()
        g.parse(file_path, format="turtle")
        self.import_graph(g, batch_size=batch_size)

    def import_nq(self, file_path: str, batch_size: int = 100) -> None:
        """Import an N-Quads RDF file by converting it to SPARQL INSERT DATA statements."""
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"N-Quads file '{file_path}' not found")

        g = ConjunctiveGraph()
        g.parse(file_path, format="nquads")
        self.import_graph(g, batch_size=batch_size)

    def import_graph(self, g: Graph, batch_size: int = 100):
        """
        Internal helper: generate and execute INSERT DATA statements from an rdflib graph.
        Handles both Graph (TTL) and ConjunctiveGraph (NQ with multiple graphs).
        """
        triples_batch = []
        for t in g.quads((None, None, None, None)) if hasattr(g, 'quads') else g:
            if len(t) == 4:  # quad
                s, p, o, graph_uri = t
                triple_str = f"{s.n3()} {p.n3()} {o.n3()} ."
                triples_batch.append((triple_str, graph_uri))
            else:  # triple
                s, p, o = t
                triple_str = f"{s.n3()} {p.n3()} {o.n3()} ."
                triples_batch.append((triple_str, None))

            if len(triples_batch) >= batch_size:
                self._execute_triple_batch(triples_batch)
                triples_batch = []

        if triples_batch:
            self._execute_triple_batch(triples_batch)

    def _execute_triple_batch(self, triples_batch):
        # Group triples by graph
        graph_map = {}
        for triple_str, g_uri in triples_batch:
            graph_map.setdefault(g_uri, []).append(triple_str)

        for g_uri, triples in graph_map.items():
            triples_str = "\n".join(triples)
            graph_iri = self._normalize_graph_identifier(g_uri)

            if graph_iri:
                query = f"INSERT DATA {{ GRAPH {graph_iri} {{ {triples_str} }} }}"
            else:
                default_graph = self._normalize_graph_identifier(self._config.default_graph)
                if default_graph:
                    query = f"INSERT DATA {{ GRAPH {default_graph} {{ {triples_str} }} }}"
                else:
                    query = f"INSERT DATA {{ {triples_str} }}"
            self.update(query)

    def _normalize_graph_identifier(self, graph_identifier):
        """
        Convert a graph identifier (URIRef, Graph, or string) into a valid SPARQL IRI literal.
        Returns None when no identifier is provided.
        """
        if not graph_identifier:
            return None

        if hasattr(graph_identifier, "identifier"):
            graph_identifier = graph_identifier.identifier

        if hasattr(graph_identifier, "n3"):
            return graph_identifier.n3()

        identifier = str(graph_identifier)
        if identifier.startswith("<") and identifier.endswith(">"):
            return identifier

        return f"<{identifier}>"

    # ---------------------
    # History and persistence
    # ---------------------
    def history(self) -> List[Event]:
        return self._history

    # ---------------------
    # Close
    # ---------------------
    def close(self):
        self._session.close()
