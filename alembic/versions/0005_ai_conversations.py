from alembic import op
import sqlalchemy as sa


revision = "0005_ai_conversations"
down_revision = "0004_companion_devices"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("companion_devices.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", name="uq_ai_conversations_device_id"),
    )
    op.create_index(
        "ix_ai_conversations_user_id", "ai_conversations", ["user_id"]
    )
    op.create_index(
        "ix_ai_conversations_user_updated",
        "ai_conversations",
        ["user_id", "updated_at"],
    )
    op.create_table(
        "ai_conversation_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("ai_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ai_conversation_messages_conversation_id",
        "ai_conversation_messages",
        ["conversation_id"],
    )
    op.create_index(
        "ix_ai_conversation_messages_conversation_created",
        "ai_conversation_messages",
        ["conversation_id", "created_at"],
    )


def downgrade():
    op.drop_index(
        "ix_ai_conversation_messages_conversation_created",
        table_name="ai_conversation_messages",
    )
    op.drop_index(
        "ix_ai_conversation_messages_conversation_id",
        table_name="ai_conversation_messages",
    )
    op.drop_table("ai_conversation_messages")
    op.drop_index(
        "ix_ai_conversations_user_updated", table_name="ai_conversations"
    )
    op.drop_index("ix_ai_conversations_user_id", table_name="ai_conversations")
    op.drop_table("ai_conversations")
