"""clean up dataset db model

Revision ID: 5a5105707147
Revises: 0b31ab4244de
Create Date: 2022-05-18 09:19:34.511867

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5a5105707147"
down_revision = "0b31ab4244de"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column("collection", "collection_type", new_column_name="dataset_type")
    op.drop_index("ix_collection_lname", table_name="collection")
    op.drop_index("ix_collection_type", table_name="collection")
    op.create_index("ix_dataset_type", "collection", ["dataset_type"], unique=False)
    op.create_unique_constraint("uix_dataset_type_lname", "collection", ["dataset_type", "lname"])
    op.drop_column("collection", "collection")

    op.execute("ALTER SEQUENCE collection_id_seq RENAME TO base_dataset_id_seq")
    op.execute("ALTER INDEX collection_pkey RENAME TO base_dataset_pkey")

    op.alter_column("contributed_values", "collection_id", new_column_name="dataset_id")
    op.drop_constraint("contributed_values_collection_id_fkey", "contributed_values", type_="foreignkey")
    op.create_foreign_key(None, "contributed_values", "collection", ["dataset_id"], ["id"], ondelete="cascade")

    # Merge the provenance column with the provenance key in extra
    op.execute(
        sa.text(
            """UPDATE collection
            SET provenance = COALESCE(extra->'provenance', '{}')::jsonb || COALESCE(provenance, '{}')::jsonb"""
        )
    )

    # Update the collection extra, with the removed fields
    op.execute(sa.text("UPDATE collection SET extra = (extra::jsonb - 'provenance')::json"))

    # Remove view columns from base collection
    op.drop_column("collection", "view_url_hdf5")
    op.drop_column("collection", "view_url_plaintext")
    op.drop_column("collection", "view_available")
    op.drop_column("collection", "view_metadata")

    # Finally rename the table
    op.rename_table("collection", "base_dataset")

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    raise RuntimeError("Cannot downgrade")
    # ### end Alembic commands ###