"""Create or update the Airbnb Cleaning service and pricing options.

This script is idempotent:
- If the service already exists, it updates core details.
- If not, it creates the service.
- It upserts the pricing options so admins can edit them in dashboard.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


SERVICE_TITLE = "Airbnb Cleaning Service"
SERVICE_SHORT = "Fast, high-standard Airbnb turnover cleaning for check-in and check-out days."
SERVICE_DESCRIPTION = (
    "Professional Airbnb Turnover Cleaning\n"
    "Done-Well delivers reliable, high-standard Airbnb cleaning designed for fast guest turnover. "
    "We combine deep-clean attention to detail with flexible scheduling that fits host calendars.\n\n"
    "What This Service Covers\n"
    "• Bathroom and kitchen sanitising\n"
    "• Linen and towel changeover support\n"
    "• Vacuuming and mopping floors\n"
    "• Dusting and trash removal\n"
    "• High-touch point disinfection (switches, handles, rails)\n\n"
    "Host-Friendly Standards\n"
    "• Suitable for both check-in and check-out windows\n"
    "• Flexible cleaner availability for busy schedules\n"
    "• Spotless finish to help protect guest ratings\n"
    "• No job is too small for our team\n\n"
    "Price Guide\n"
    "Hourly rates typically start around £21–£25 per hour depending on service level and room count.\n"
    "Select a room package below to request this service."
)

DEEP_TIERS = [
    ("1 Room Turnover", 20.00, 1, 0.0, 0.0),
    ("2 Rooms Turnover", 35.00, 1, 0.0, 0.0),
    ("3-4 Rooms Turnover", 50.00, 2, 0.0, 0.0),
    ("5 Rooms Turnover", 55.00, 2, 0.0, 0.0),
    ("Laundry and Ironing Add-on", 19.50, 1, 0.0, 0.0),
]


def _db_engine():
    return (os.getenv("DB_ENGINE") or "mysql").strip().lower()


def _connect(engine: str):
    if engine == "postgres":
        import psycopg2

        return psycopg2.connect(os.getenv("POSTGRES_URL"))

    import mysql.connector

    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DB"),
    )


def _fetch_existing_service_id(cursor, engine: str):
    if engine == "postgres":
        cursor.execute(
            """
            SELECT id
            FROM services
            WHERE LOWER(COALESCE(title, '')) IN ('air bnb cleaning service', 'airbnb cleaning service')
               OR LOWER(COALESCE(name, '')) IN ('air bnb cleaning service', 'airbnb cleaning service')
            ORDER BY id ASC
            LIMIT 1
            """
        )
    else:
        cursor.execute(
            """
            SELECT id
            FROM services
            WHERE LOWER(COALESCE(title, '')) IN ('air bnb cleaning service', 'airbnb cleaning service')
               OR LOWER(COALESCE(name, '')) IN ('air bnb cleaning service', 'airbnb cleaning service')
            ORDER BY id ASC
            LIMIT 1
            """
        )

    row = cursor.fetchone()
    if not row:
        return None
    return row[0] if isinstance(row, tuple) else row.get("id")


def _get_column_type(cursor, engine: str, table_name: str, column_name: str):
    if engine == "postgres":
        cursor.execute(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            LIMIT 1
            """,
            (table_name, column_name),
        )
        row = cursor.fetchone()
        return (row[0] if row else "").lower()

    cursor.execute(
        """
        SELECT DATA_TYPE
        FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s
        LIMIT 1
        """,
        (table_name, column_name),
    )
    row = cursor.fetchone()
    if isinstance(row, tuple):
        return (row[0] or "").lower()
    return (row.get("DATA_TYPE") if row else "").lower()


def _flag_value(dtype: str):
    dtype = (dtype or "").lower()
    return True if dtype in ("boolean", "bool") else 1


def _upsert_service(cursor, engine: str):
    existing_id = _fetch_existing_service_id(cursor, engine)
    allow_multiselect_type = _get_column_type(cursor, engine, "services", "allow_multiselect")
    is_active_type = _get_column_type(cursor, engine, "services", "is_active")
    allow_multiselect_value = _flag_value(allow_multiselect_type)
    is_active_value = _flag_value(is_active_type)

    if existing_id:
        cursor.execute(
            """
            UPDATE services
            SET title = %s,
                name = %s,
                short_description = %s,
                description = %s,
                price = %s,
                discount_threshold = %s,
                discount_percent = %s,
                pricing_model = %s,
                table_header_col1 = %s,
                table_header_col2 = %s,
                table_header_col3 = %s,
                allow_multiselect = %s,
                is_active = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                SERVICE_TITLE,
                SERVICE_TITLE,
                SERVICE_SHORT,
                SERVICE_DESCRIPTION,
                None,
                None,
                None,
                "airbnb",
                "Property Type",
                "Standard Price",
                "Upgrade Option",
                allow_multiselect_value,
                is_active_value,
                existing_id,
            ),
        )
        return existing_id, False

    if engine == "postgres":
        cursor.execute(
            """
            INSERT INTO services (
                title, name, short_description, description,
                price, discount_threshold, discount_percent,
                pricing_model, table_header_col1, table_header_col2, table_header_col3,
                allow_multiselect, image_path, is_active
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                SERVICE_TITLE,
                SERVICE_TITLE,
                SERVICE_SHORT,
                SERVICE_DESCRIPTION,
                None,
                None,
                None,
                "airbnb",
                "Property Type",
                "Standard Price",
                "Upgrade Option",
                allow_multiselect_value,
                None,
                is_active_value,
            ),
        )
        new_id = cursor.fetchone()[0]
        return new_id, True

    cursor.execute(
        """
        INSERT INTO services (
            title, name, short_description, description,
            price, discount_threshold, discount_percent,
            pricing_model, table_header_col1, table_header_col2, table_header_col3,
            allow_multiselect, image_path, is_active
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            SERVICE_TITLE,
            SERVICE_TITLE,
            SERVICE_SHORT,
            SERVICE_DESCRIPTION,
            None,
            None,
            None,
            "airbnb",
            "Property Type",
            "Standard Price",
            "Upgrade Option",
            allow_multiselect_value,
            None,
            is_active_value,
        ),
    )
    return cursor.lastrowid, True


def _sync_deep_tiers(cursor, service_id, engine: str):
    cursor.execute(
        """
        SELECT id, tier_name
        FROM service_pricing_tiers
        WHERE service_id = %s
        """,
        (service_id,),
    )
    existing_rows = cursor.fetchall() or []
    existing_by_name = {}
    for row in existing_rows:
        row_id = row[0] if isinstance(row, tuple) else row.get("id")
        row_name = row[1] if isinstance(row, tuple) else row.get("tier_name")
        if row_name:
            existing_by_name[row_name] = row_id

    keep_ids = set()

    for tier_name, hourly_rate, min_staff, equipment_fee, detergent_fee in DEEP_TIERS:
        existing_id = existing_by_name.get(tier_name)
        if existing_id:
            cursor.execute(
                """
                UPDATE service_pricing_tiers
                SET hourly_rate = %s,
                    min_staff = %s,
                    equipment_fee = %s,
                    detergent_fee = %s
                WHERE id = %s
                """,
                (hourly_rate, min_staff, equipment_fee, detergent_fee, existing_id),
            )
            keep_ids.add(existing_id)
        else:
            if engine == "postgres":
                cursor.execute(
                    """
                    INSERT INTO service_pricing_tiers
                    (service_id, tier_name, hourly_rate, min_staff, equipment_fee, detergent_fee)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (service_id, tier_name, hourly_rate, min_staff, equipment_fee, detergent_fee),
                )
                keep_ids.add(cursor.fetchone()[0])
            else:
                cursor.execute(
                    """
                    INSERT INTO service_pricing_tiers
                    (service_id, tier_name, hourly_rate, min_staff, equipment_fee, detergent_fee)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (service_id, tier_name, hourly_rate, min_staff, equipment_fee, detergent_fee),
                )
                if getattr(cursor, "lastrowid", None):
                    keep_ids.add(cursor.lastrowid)

    if keep_ids:
        placeholders = ", ".join(["%s"] * len(keep_ids))
        cursor.execute(
            f"DELETE FROM service_pricing_tiers WHERE service_id = %s AND id NOT IN ({placeholders})",
            (service_id, *list(keep_ids)),
        )
    else:
        cursor.execute("DELETE FROM service_pricing_tiers WHERE service_id = %s", (service_id,))


def _cleanup_legacy_pricing(cursor, service_id):
    cursor.execute("DELETE FROM service_options WHERE service_id = %s", (service_id,))
    cursor.execute("DELETE FROM service_pricing_items WHERE service_id = %s", (service_id,))
    cursor.execute("DELETE FROM service_tenancy_rates WHERE service_id = %s", (service_id,))


def main():
    engine = _db_engine()
    print(f"DB Engine: {engine}")

    conn = _connect(engine)
    cursor = conn.cursor(dictionary=True) if engine != "postgres" else conn.cursor()

    try:
        service_id, created = _upsert_service(cursor, engine)
        _cleanup_legacy_pricing(cursor, service_id)
        _sync_deep_tiers(cursor, service_id, engine)
        conn.commit()
        action = "created" if created else "updated"
        print(f"Airbnb service {action} successfully (service_id={service_id}).")
        print("Deep pricing tiers synced successfully.")
        print("Legacy duplicate options/items/tenancy rates removed.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
