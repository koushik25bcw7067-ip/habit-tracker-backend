"""add day planner"""

from alembic import op
import sqlalchemy as sa


# IMPORTANT: Alembic migration identifiers
revision = "edf84ebac12a"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "day_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("main_goal", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_day_plans_plan_date"),
        "day_plans",
        ["plan_date"],
        unique=False,
    )

    op.create_index(
        op.f("ix_day_plans_user_id"),
        "day_plans",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "day_plan_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day_plan_id", sa.Integer(), nullable=False),
        sa.Column("time", sa.Time(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "TASK",
                "HABIT",
                "PERSONAL",
                name="day_plan_item_type_enum",
            ),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Enum(
                "LOW",
                "MEDIUM",
                "HIGH",
                name="day_plan_priority_enum",
            ),
            nullable=False,
        ),
        sa.Column("reminder", sa.Boolean(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["day_plan_id"],
            ["day_plans.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_day_plan_items_day_plan_id"),
        "day_plan_items",
        ["day_plan_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_day_plan_items_day_plan_id"),
        table_name="day_plan_items",
    )

    op.drop_table("day_plan_items")

    op.drop_index(
        op.f("ix_day_plans_user_id"),
        table_name="day_plans",
    )

    op.drop_index(
        op.f("ix_day_plans_plan_date"),
        table_name="day_plans",
    )

    op.drop_table("day_plans")