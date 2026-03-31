import os

PROJECT_NAME = os.environ.get("PROJECT_NAME", "default")
MEMGRAPH_URI = os.environ.get("MEMGRAPH_URI", "bolt://localhost:7687")
MCP_PORT = int(os.environ.get("MCP_PORT", "6001"))
FULL_METADATA_RELOAD = os.environ.get("FULL_METADATA_RELOAD", "false").lower() in ("true", "1", "yes")
LOAD_BSL_SIGNATURES = os.environ.get("LOAD_BSL_SIGNATURES", "true").lower() in ("true", "1", "yes")
LOAD_FORMS_FROM_XML = os.environ.get("LOAD_FORMS_FROM_XML", "true").lower() in ("true", "1", "yes")
LOAD_PREDEFINED_VALUES = os.environ.get("LOAD_PREDEFINED_VALUES", "true").lower() in ("true", "1", "yes")
LOAD_ROLE_RIGHTS = os.environ.get("LOAD_ROLE_RIGHTS", "true").lower() in ("true", "1", "yes")

DATA_DIR = "/app/data"
METADATA_DIR = os.path.join(DATA_DIR, "metadata")
CODE_DIR = os.path.join(DATA_DIR, "code")
