from alembic import op
import sqlalchemy as sa


revision = "0004_companion_devices"
down_revision = "0003_tasks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "companion_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("pairing_code_hash", sa.String(length=64), nullable=True),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credential_hash", sa.String(length=64), nullable=True),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_action", sa.JSON(), nullable=True),
        sa.Column("pending_action_id", sa.String(length=64), nullable=True),
        sa.Column(
            "pending_action_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("web_session_hash", sa.String(length=64), nullable=True),
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_action_type", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("credential_hash", name="uq_companion_devices_credential_hash"),
    )
    op.create_index(
        "ix_companion_devices_user_id", "companion_devices", ["user_id"]
    )
    op.create_index(
        "ix_companion_devices_pairing_code_hash",
        "companion_devices",
        ["pairing_code_hash"],
    )
    op.create_index(
        "uq_companion_devices_user_web_session_hash",
        "companion_devices",
        ["user_id", "web_session_hash"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "ix_companion_devices_pairing_code_hash", table_name="companion_devices"
    )
    op.drop_index(
        "uq_companion_devices_user_web_session_hash",
        table_name="companion_devices",
    )
    op.drop_index("ix_companion_devices_user_id", table_name="companion_devices")
    op.drop_table("companion_devices")
