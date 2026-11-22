"""Initial parcel schema, immutable outbox and seeded parcel types."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parcel_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
    )
    op.bulk_insert(
        sa.table("parcel_types", sa.column("id", sa.Integer()), sa.column("name", sa.String())),
        [
            {"id": 1, "name": "одежда"},
            {"id": 2, "name": "электроника"},
            {"id": 3, "name": "разное"},
        ],
    )
    op.create_table(
        "parcels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("weight_kg", sa.Numeric(12, 3), nullable=False),
        sa.Column("type_id", sa.Integer(), sa.ForeignKey("parcel_types.id"), nullable=False),
        sa.Column("declared_value_usd", sa.Numeric(16, 2), nullable=False),
        sa.Column("delivery_cost_rub", sa.Numeric(24, 2)),
        sa.Column("exchange_rate", sa.Numeric(18, 8)),
        sa.Column("rate_date", sa.String(40)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("carrier_id", sa.BigInteger()),
        sa.Column("assignment_token_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("weight_kg > 0 AND declared_value_usd >= 0"),
        sa.CheckConstraint("carrier_id IS NULL OR carrier_id > 0"),
        sa.CheckConstraint("status IN ('pending', 'calculated', 'failed')"),
    )
    op.create_index("ix_parcels_owner_created", "parcels", ["owner_id", "created_at", "id"])
    op.create_index(
        "ix_parcels_owner_type_status",
        "parcels",
        ["owner_id", "type_id", "status", "created_at", "id"],
    )
    op.create_table(
        "calculation_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("parcel_id", sa.Uuid(), sa.ForeignKey("parcels.id"), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_calculation_outbox_delivered_at", "calculation_outbox", ["delivered_at"])


def downgrade() -> None:
    op.drop_table("calculation_outbox")
    op.drop_table("parcels")
    op.drop_table("parcel_types")
