"""Schedule recovery of unfinished parcel calculations."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("parcels", sa.Column("recover_after", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE parcels SET recover_after = CURRENT_TIMESTAMP WHERE status IN ('pending', 'failed')"
    )
    op.create_index(
        "ix_parcels_recover_after",
        "parcels",
        ["recover_after", "id"],
        postgresql_where=sa.text("recover_after IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_parcels_recover_after", table_name="parcels")
    op.drop_column("parcels", "recover_after")
