"""
Schema Profiler Data Module
Loads and provides database schema information from schema_profiler.json

This module serves as the single source of truth for database schema,
replacing hardcoded schema definitions throughout the codebase.
"""
import json
import os
from typing import Dict, List, Any

# Path to schema profiler JSON
# Resolve relative to project root (we're in apps/fastapi/src/services/)
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
# Go up 4 levels: services -> src -> fastapi -> apps -> project_root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_MODULE_DIR))))
SCHEMA_PROFILER_PATH = os.path.join(_PROJECT_ROOT, 'schema_profiler.json')


def _load_schema_profiler() -> Dict[str, Any]:
    """Load schema profiler JSON file"""
    try:
        with open(SCHEMA_PROFILER_PATH, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        # Fallback to empty schema if file not found
        return {"tables": {}}


# Load schema at module import time
_SCHEMA_PROFILER = _load_schema_profiler()


def get_raw_schema() -> Dict[str, Any]:
    """Get the raw schema profiler data"""
    return _SCHEMA_PROFILER


def get_tables() -> List[str]:
    """Get list of all table names"""
    return list(_SCHEMA_PROFILER.get("tables", {}).keys())


def get_table_schema(table_name: str) -> Dict[str, Any]:
    """Get schema for a specific table"""
    return _SCHEMA_PROFILER.get("tables", {}).get(table_name, {})


def get_column_type(table_name: str, column_name: str) -> str:
    """Get the type of a specific column"""
    table = get_table_schema(table_name)
    for col in table.get("columns", []):
        if col["name"] == column_name:
            return col["type"]
    return None


def is_column_nullable(table_name: str, column_name: str) -> bool:
    """Check if a column is nullable"""
    table = get_table_schema(table_name)
    for col in table.get("columns", []):
        if col["name"] == column_name:
            return col.get("nullable", False)
    return None


def get_foreign_keys(table_name: str) -> List[Dict[str, str]]:
    """Get all foreign keys for a table"""
    table = get_table_schema(table_name)
    fks = []
    for col in table.get("columns", []):
        fk = col.get("foreign_key")
        if fk:
            # Parse FK reference like "recipe.id" or "user.uid"
            if "." in fk:
                ref_table, ref_column = fk.split(".")
                fks.append({
                    "column": col["name"],
                    "references_table": ref_table,
                    "references_column": ref_column
                })
    return fks


def get_all_relationships() -> List[Dict[str, str]]:
    """
    Extract all foreign key relationships from schema profiler

    Returns list of: {from, from_column, to, to_column}
    """
    relationships = []
    tables = _SCHEMA_PROFILER.get("tables", {})

    for table_name, table_data in tables.items():
        for col in table_data.get("columns", []):
            fk = col.get("foreign_key")
            if fk and "." in fk:
                ref_table, ref_column = fk.split(".", 1)
                relationships.append({
                    "from": table_name,
                    "from_column": col["name"],
                    "to": ref_table,
                    "to_column": ref_column
                })

    return relationships


# SCHEMA_DATA in format expected by schema_understanding.py
# This provides backward compatibility with existing code
SCHEMA_DATA = {}

for table_name, table_data in _SCHEMA_PROFILER.get("tables", {}).items():
    SCHEMA_DATA[table_name] = {
        "columns": table_data.get("columns", []),
        "description": f"Table {table_name}"
    }


def get_schema_summary() -> Dict[str, Any]:
    """Get a summary of the database schema"""
    tables = _SCHEMA_PROFILER.get("tables", {})
    return {
        "database_name": _SCHEMA_PROFILER.get("database_name"),
        "schema": _SCHEMA_PROFILER.get("schema"),
        "total_tables": len(tables),
        "total_columns": sum(len(t.get("columns", [])) for t in tables.values()),
        "tables": list(tables.keys())
    }


if __name__ == "__main__":
    # Test the module
    print("Schema Profiler Data Module")
    print("=" * 50)
    print(f"Database: {_SCHEMA_PROFILER.get('database_name')}")
    print(f"Schema: {_SCHEMA_PROFILER.get('schema')}")
    print(f"Total Tables: {len(get_tables())}")
    print(f"\nTables: {', '.join(get_tables()[:10])}...")
    print(f"\nTotal Relationships: {len(get_all_relationships())}")
