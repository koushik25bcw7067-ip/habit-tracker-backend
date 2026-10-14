from alembic import op
import sqlalchemy as sa


revision = "0002_user_themes"
down_revision = "edf84ebac12a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_themes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("preset_key", sa.String(length=40), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_user_themes_user_id"),
    )


def downgrade():
    op.drop_table("user_themes")
