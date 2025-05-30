"""add available column on task queue

Revision ID: 12e2ba353ee6
Revises: 73b4838a6839
Create Date: 2024-10-18 10:31:43.514061

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "12e2ba353ee6"
down_revision = "73b4838a6839"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column("task_queue", sa.Column("available", sa.Boolean(), nullable=True))

    op.execute(
        """
            UPDATE task_queue tq
            SET available = CASE WHEN br.status = 'waiting' THEN TRUE ELSE FALSE END
            FROM base_record br
            WHERE tq.record_id = br.id;
    """
    )

    op.drop_index("ix_task_queue_sort", table_name="task_queue")
    op.execute(
        "CREATE INDEX ix_task_queue_sort ON task_queue (priority DESC, sort_date, id, tag) WHERE available = True;"
    )

    op.alter_column("task_queue", "available", nullable=False)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("ix_task_queue_sort", table_name="task_queue")
    op.execute("CREATE INDEX ix_task_queue_sort ON task_queue (priority DESC, sort_date, id, tag);")

    op.drop_column("task_queue", "available")
    # ### end Alembic commands ###
