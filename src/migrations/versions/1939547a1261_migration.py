"""Migration

Revision ID: 1939547a1261
Revises: 5398765db926
Create Date: 2024-04-25 20:44:08.880833

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "1939547a1261"
down_revision = "5398765db926"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "codebase_namespace_mutex",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["codebase_namespaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("codebase_namespace_mutex")
    # ### end Alembic commands ###
