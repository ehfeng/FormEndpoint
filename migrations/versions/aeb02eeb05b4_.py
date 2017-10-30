"""empty message

Revision ID: aeb02eeb05b4
Revises: b3ac40388cc2
Create Date: 2017-10-30 01:44:03.032486

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'aeb02eeb05b4'
down_revision = 'b3ac40388cc2'
branch_labels = None
depends_on = None


def upgrade():
    if not op.get_context().as_sql:
        connection = op.get_bind()
        connection.execution_options(isolation_level='AUTOCOMMIT')

    op.execute('ALTER TYPE types RENAME TO destination_types')
    op.execute('ALTER TYPE destination_types ADD VALUE \'GoogleSheet\'')



def downgrade():
    pass
