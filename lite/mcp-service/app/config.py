import os

PROJECT_NAME = os.environ.get("PROJECT_NAME", "default")
MEMGRAPH_URI = os.environ.get("MEMGRAPH_URI", "bolt://localhost:7687")
MCP_PORT = int(os.environ.get("MCP_PORT", "6001"))
FULL_METADATA_RELOAD = os.environ.get("FULL_METADATA_RELOAD", "false").lower() in ("true", "1", "yes")
LOAD_BSL_SIGNATURES = os.environ.get("LOAD_BSL_SIGNATURES", "true").lower() in ("true", "1", "yes")
LOAD_FORMS_FROM_XML = os.environ.get("LOAD_FORMS_FROM_XML", "true").lower() in ("true", "1", "yes")
LOAD_PREDEFINED_VALUES = os.environ.get("LOAD_PREDEFINED_VALUES", "true").lower() in ("true", "1", "yes")
LOAD_ROLE_RIGHTS = os.environ.get("LOAD_ROLE_RIGHTS", "true").lower() in ("true", "1", "yes")

# Embedding (optional — disabled by default, runs in-process via ONNX)
ENABLE_EMBEDDING = os.environ.get("ENABLE_EMBEDDING", "false").lower() in ("true", "1", "yes")
EMBEDDING_MODEL_PATH = os.environ.get("EMBEDDING_MODEL_PATH", "/app/models/e5-base")
EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "64"))
EMBEDDING_QUERY_PREFIX = os.environ.get("EMBEDDING_QUERY_PREFIX", "query: ")
EMBEDDING_PASSAGE_PREFIX = os.environ.get("EMBEDDING_PASSAGE_PREFIX", "passage: ")
RERANKER_MODEL_PATH = os.environ.get("RERANKER_MODEL_PATH", "/app/models/cross-encoder")

DATA_DIR = "/app/data"
METADATA_DIR = os.path.join(DATA_DIR, "metadata")
CODE_DIR = os.path.join(DATA_DIR, "code")
EMBEDDING_DB_PATH = os.path.join(DATA_DIR, "embeddings.db")
