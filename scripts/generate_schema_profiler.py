#!/usr/bin/env python
"""
Generate schema_profiler.json and schema_profiler_data.py from database
Only includes allowed tables (excludes forbidden ones)
"""
import os
import sys
import json
from typing import Dict, List, Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

load_dotenv()

from libs.utils.logger import setup_logger

logger = setup_logger("generate_schema_profiler", True, False, False, False)
FORBIDDEN_TABLES = {
    'collection', 'collection_recipes_recipe',
    'comment',
    'menu', 'menu_recipe', 'menu_type', 'menu_type_translation', 'menu_view',
    'notification',
    'push_token',
    'subscription',
    'user_stored_ingredient',
    'video', 'video_recipes_recipe', 'video_tags_tag',
    'query-result-cache',
    'recipe_liked_by_user',
    'recipe_preference', 'recipe_preference_translation', 'recipe_recipe_preferences_recipe_preference',
    'recipe_view',
    'recipe_embedding', 'ingredient_embedding',  # Old separate embedding tables
}

# Tables to include
ALLOWED_TABLES = {
    'chat_session', 'chat_message',  # New chatbot tables
    'recipe', 'recipe_ingredient', 'recipe_instruction', 'recipe_instruction_selected_ingredients_ingredient',
    'recipe_seasonality', 'recipe_tags_tag', 'recipe_type', 'recipe_type_translation', 'recipe_recipe_types_recipe_type',
    'ingredient', 'ingredient_macros', 'ingredient_micros', 'ingredient_pricing',
    'seasonality', 'seasonality_translation',
    'tag',
    'language',
    'country', 'currency',
    'measuring_unit', 'measuring_unit_translation',
    'user', 'user_purchase', 'user_likes_recipe',
    'like',
    'bundle', 'bundle_price', 'bundle_recipe',
    'migrations',
}

# Connection
conn = psycopg2.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    port=int(os.getenv('DB_PORT', 5432)),
    database=os.getenv('DB_NAME', 'sulten-db'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD', 'postgres')
)

def get_table_info(table_name: str) -> Dict[str, Any]:
    """Get table information including columns"""
    cursor = conn.cursor()

    # Get columns
    cursor.execute("""
        SELECT
            column_name,
            data_type,
            is_nullable,
            column_default,
            character_maximum_length,
            numeric_precision,
            numeric_scale
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (table_name,))

    columns = []
    primary_keys = set()

    for row in cursor.fetchall():
        col_name, data_type, is_nullable, default, char_max, numeric_prec, numeric_scale = row

        # Map PostgreSQL types to output types
        type_mapping = {
            'uuid': 'UUID',
            'character varying': 'VARCHAR',
            'text': 'TEXT',
            'timestamp without time zone': 'TIMESTAMP',
            'timestamp with time zone': 'TIMESTAMPTZ',
            'integer': 'INTEGER',
            'bigint': 'BIGINT',
            'double precision': 'DOUBLE PRECISION',
            'numeric': 'NUMERIC',
            'boolean': 'BOOLEAN',
            'jsonb': 'JSONB',
            'json': 'JSON',
            'tsvector': 'TSVECTOR',
            'vector': 'VECTOR',
        }

        mapped_type = type_mapping.get(data_type.lower(), data_type.upper())

        column_info = {
            "name": col_name,
            "type": mapped_type,
            "nullable": is_nullable == 'YES',
            "default": default,
            "autoincrement": False,
            "is_primary_key": False,
            "description": "",
            "foreign_key": None
        }

        # Add size info for VARCHAR and NUMERIC
        if mapped_type == 'VARCHAR' and char_max:
            column_info["type"] = f"VARCHAR({char_max})"
        elif mapped_type == 'NUMERIC' and numeric_prec:
            if numeric_scale:
                column_info["type"] = f"NUMERIC({numeric_prec}, {numeric_scale})"
            else:
                column_info["type"] = f"NUMERIC({numeric_prec})"
        elif mapped_type == 'VECTOR':
            # Get vector size
            cursor2 = conn.cursor()
            cursor2.execute(f"""
                SELECT pg_attribute.typmod
                FROM pg_attribute, pg_class, pg_type
                WHERE pg_class.relname = '{table_name}'
                AND pg_attribute.attname = '{col_name}'
                AND pg_class.oid = pg_attribute.attrelid
                AND pg_type.oid = pg_attribute.atttypid
            """)
            result = cursor2.fetchone()
            if result and result[0] > 0:
                # typmod for vector is dimension + 1
                dimension = result[0] - 1
                column_info["type"] = f"VECTOR({dimension})"
            cursor2.close()

        columns.append(column_info)

    # Get primary keys
    cursor.execute("""
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = '%s'::regclass AND i.indisprimary
    """ % table_name)

    for row in cursor.fetchall():
        pk_name = row[0]
        primary_keys.add(pk_name)
        for col in columns:
            if col["name"] == pk_name:
                col["is_primary_key"] = True
                break

    # Get foreign keys
    cursor.execute("""
        SELECT
            kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
            AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_name = %s
    """, (table_name,))

    for row in cursor.fetchall():
        col_name, foreign_table, foreign_col = row
        for col in columns:
            if col["name"] == col_name:
                col["foreign_key"] = f"{foreign_table}.{foreign_col}"
                break

    cursor.close()

    return {
        "table_name": table_name,
        "description": f"Table {table_name}",
        "columns": columns
    }

def generate_json():
    """Generate schema_profiler.json"""
    cursor = conn.cursor()

    # Get all tables
    cursor.execute("""
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    """)

    tables = {}
    for row in cursor.fetchall():
        table_name = row[0]
        if table_name in FORBIDDEN_TABLES:
            continue
        if table_name not in ALLOWED_TABLES:
            continue

        tables[table_name] = get_table_info(table_name)

    output = {
        "database_name": "sulten-db",
        "schema": "public",
        "enum_types": {},
        "tables": tables
    }

    with open('schema_profiler.json', 'w') as f:
        json.dump(output, f, indent=2)

    logger.info("Generated schema_profiler.json")
    cursor.close()

def generate_data_py():
    """Generate schema_profiler_data.py"""
    cursor = conn.cursor()

    # Get all tables
    cursor.execute("""
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    """)

    tables_data = {}
    for row in cursor.fetchall():
        table_name = row[0]
        if table_name in FORBIDDEN_TABLES:
            continue
        if table_name not in ALLOWED_TABLES:
            continue

        # Get columns
        cursor.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (table_name,))

        columns = []
        for col_row in cursor.fetchall():
            col_name, data_type = col_row
            columns.append({
                "name": col_name,
                "type": data_type
            })

        tables_data[table_name] = {
            "columns": columns
        }

    output = f'''# Auto-generated schema data
# Generated from database

SCHEMA_DATA = {json.dumps(tables_data, indent=4)}
'''

    with open('schema_profiler_data.py', 'w') as f:
        f.write(output)

    logger.info("✓ Generated schema_profiler_data.py")
    cursor.close()

def main():
    logger.info("Generating schema profiler files...")
    generate_json()
    generate_data_py()
    logger.info("Done!")

if __name__ == "__main__":
    main()
    conn.close()
