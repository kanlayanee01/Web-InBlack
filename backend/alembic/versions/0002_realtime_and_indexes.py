"""NOTIFY on every order event, plus indexes for dashboard figures.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

STATEMENTS = [
    # NOTIFY is delivered only when the transaction commits, so listeners can never see an event
    # that later rolls back, and a crash right after commit cannot lose the signal.
    """
    CREATE FUNCTION notify_order_event() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM pg_notify('order_events', NEW.order_id);
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER order_events_notify AFTER INSERT ON order_events
    FOR EACH ROW EXECUTE FUNCTION notify_order_event()
    """,
    "CREATE INDEX ledger_created_idx ON ledger_entries (created_at)",
]


def upgrade() -> None:
    connection = op.get_bind()
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError("recreate the database instead")
