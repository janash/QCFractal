"""migrate wavefunction table

Revision ID: 05f7b00bfac8
Revises: fd76a1459fa3
Create Date: 2021-10-13 16:59:18.738338

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "05f7b00bfac8"
down_revision = "fd76a1459fa3"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column("wavefunction_store", sa.Column("orbitals_a", sa.String(), nullable=True))
    op.add_column("wavefunction_store", sa.Column("orbitals_b", sa.String(), nullable=True))
    op.add_column("wavefunction_store", sa.Column("density_a", sa.String(), nullable=True))
    op.add_column("wavefunction_store", sa.Column("density_b", sa.String(), nullable=True))
    op.add_column("wavefunction_store", sa.Column("fock_a", sa.String(), nullable=True))
    op.add_column("wavefunction_store", sa.Column("fock_b", sa.String(), nullable=True))
    op.add_column("wavefunction_store", sa.Column("eigenvalues_a", sa.String(), nullable=True))
    op.add_column("wavefunction_store", sa.Column("eigenvalues_b", sa.String(), nullable=True))
    op.add_column("wavefunction_store", sa.Column("occupations_a", sa.String(), nullable=True))
    op.add_column("wavefunction_store", sa.Column("occupations_b", sa.String(), nullable=True))
    op.drop_column("wavefunction_store", "extras")
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column("wavefunction_store", sa.Column("extras", postgresql.BYTEA(), autoincrement=False, nullable=True))
    op.drop_column("wavefunction_store", "occupations_b")
    op.drop_column("wavefunction_store", "occupations_a")
    op.drop_column("wavefunction_store", "eigenvalues_b")
    op.drop_column("wavefunction_store", "eigenvalues_a")
    op.drop_column("wavefunction_store", "fock_b")
    op.drop_column("wavefunction_store", "fock_a")
    op.drop_column("wavefunction_store", "density_b")
    op.drop_column("wavefunction_store", "density_a")
    op.drop_column("wavefunction_store", "orbitals_b")
    op.drop_column("wavefunction_store", "orbitals_a")
    # ### end Alembic commands ###