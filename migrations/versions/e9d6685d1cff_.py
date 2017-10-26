"""empty message

Revision ID: e9d6685d1cff
Revises: 1233098328ad
Create Date: 2017-10-21 15:29:43.657277

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e9d6685d1cff'
down_revision = '1233098328ad'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('endpoint', sa.Column('public', sa.Boolean(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('endpoint', 'public')
    # ### end Alembic commands ###