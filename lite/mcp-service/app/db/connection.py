import logging
from neo4j import GraphDatabase

from app.config import MEMGRAPH_URI

log = logging.getLogger(__name__)

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(MEMGRAPH_URI, auth=None)
        _driver.verify_connectivity()
        log.info("Connected to Memgraph at %s", MEMGRAPH_URI)
    return _driver


def run_query(cypher: str, params: dict | None = None) -> list[dict]:
    driver = get_driver()
    with driver.session() as session:
        result = session.run(cypher, params or {})
        return [dict(record) for record in result]


def run_write(cypher: str, params: dict | None = None):
    driver = get_driver()
    with driver.session() as session:
        session.run(cypher, params or {})


def close():
    global _driver
    if _driver:
        _driver.close()
        _driver = None
