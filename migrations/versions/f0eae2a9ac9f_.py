"""empty message

Revision ID: f0eae2a9ac9f
Revises: 
Create Date: 2017-10-01 18:43:03.295892

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f0eae2a9ac9f'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('user',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('username', sa.String(), nullable=True),
    sa.Column('verified', sa.Boolean(), nullable=True),
    sa.Column('credentials_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('validation_hash', sa.Text(), nullable=True),
    sa.Column('validation_hash_added', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email'),
    sa.UniqueConstraint('username')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('user')
    # ### end Alembic commands ###