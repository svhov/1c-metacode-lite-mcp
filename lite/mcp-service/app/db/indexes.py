import logging

from app.db.connection import run_write

log = logging.getLogger(__name__)

LABELS_WITH_QN = [
    "MetadataObject", "MetadataCategory", "Configuration",
    "Attribute", "Resource", "Dimension", "Characteristic",
    "TabularPart", "EnumValue", "PredefinedItem",
    "Form", "FormControl", "FormAttribute", "FormEvent",
    "Command", "Layout", "Module", "Routine",
    "EventSubscription", "JournalGraph",
    "UrlTemplate", "UrlMethod",
    "AccountingFlag", "DimensionAccountingFlag",
]


def ensure_indexes():
    log.info("Creating indexes...")
    # Index on name for all labelled nodes
    for label in LABELS_WITH_QN:
        try:
            run_write(f"CREATE INDEX ON :{label}(name)")
        except Exception:
            pass
        try:
            run_write(f"CREATE INDEX ON :{label}(qualified_name)")
        except Exception:
            pass

    # Project node
    try:
        run_write("CREATE INDEX ON :Project(name)")
    except Exception:
        pass

    # Routine-specific indexes
    for prop in ("owner_qn", "project_name", "config_name", "id"):
        try:
            run_write(f"CREATE INDEX ON :Routine({prop})")
        except Exception:
            pass

    # Module indexes
    for prop in ("owner_qn", "project_name", "id"):
        try:
            run_write(f"CREATE INDEX ON :Module({prop})")
        except Exception:
            pass

    # MetadataObject project/category indexes
    for prop in ("project_name", "category_name", "config_name"):
        try:
            run_write(f"CREATE INDEX ON :MetadataObject({prop})")
        except Exception:
            pass

    log.info("Indexes created")
