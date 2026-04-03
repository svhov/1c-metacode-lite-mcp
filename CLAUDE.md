# 1C Metacode MCP Lite

## Project structure

```
lite/
  docker-compose.yml          # Memgraph + MCP services
  mcp-service/
    Dockerfile                # Python 3.12 slim
    requirements.txt
    app/
      main.py                 # FastMCP server entry point (SSE transport)
      config.py               # Environment config
      db/
        connection.py          # Memgraph driver (neo4j Python driver, auth=None)
        indexes.py             # Graph index creation
        loader.py              # Data orchestration
      parsers/
        metadata_report.py     # 1C configuration report parser (UTF-16/UTF-8)
        bsl_parser.py          # BSL code parser (procedures, functions, calls)
        form_parser.py         # Form.xml parser (controls, events, attributes)
        predefined_parser.py   # Predefined.xml parser
        roles_parser.py        # Rights.xml parser (role access)
        config_dump.py         # ConfigDumpInfo.xml GUID mapping
      tools/
        search_metadata.py     # 57 template-based metadata query operations
        search_code.py         # BSL code search
        search_description.py  # Fulltext metadata search
```

## Architecture

- **Memgraph** (C++) -- graph DB, shared by all MCP services
- **MCP services** -- Python 3.12 + FastMCP, SSE transport
- All projects share one Memgraph, isolated by `project_name`
- No LLM, no embeddings -- pure template Cypher queries

## Key technical notes

- Memgraph uses Neo4j Bolt driver (`neo4j` Python package) with `auth=None`
- Memgraph `toLower()` doesn't work with Cyrillic -- search uses Python-side variants
- `CONTAINS` predicate instead of fulltext index (Memgraph has no native fulltext)
- Data loads in background thread so MCP server starts immediately
