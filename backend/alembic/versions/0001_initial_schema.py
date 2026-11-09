"""Initial schema.

The rules that must never break live here as database constraints, so that they hold even if
application code has a bug or two requests race each other.

Custom SQLSTATE codes raised by triggers:
  IB001  illegal order status transition
  IB002  attempt to change an append-only table

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ORDER_STATUSES = (
    "'PENDING_PAYMENT','PAID','IN_QUEUE','IN_PROGRESS','COMPLETED',"
    "'EXPIRED','PAYMENT_FAILED','FAILED','REFUNDED','NEEDS_REVIEW'"
)

TRANSITIONS = [
    ("PENDING_PAYMENT", "PAID"),
    ("PENDING_PAYMENT", "EXPIRED"),
    ("PENDING_PAYMENT", "PAYMENT_FAILED"),
    ("PAID", "IN_QUEUE"),
    ("IN_QUEUE", "IN_PROGRESS"),
    ("IN_PROGRESS", "COMPLETED"),
    ("IN_PROGRESS", "FAILED"),
    ("FAILED", "REFUNDED"),
    ("COMPLETED", "REFUNDED"),
    ("EXPIRED", "PAID"),
    ("EXPIRED", "REFUNDED"),
    ("FAILED", "IN_QUEUE"),
    ("NEEDS_REVIEW", "IN_QUEUE"),
    ("NEEDS_REVIEW", "COMPLETED"),
    ("NEEDS_REVIEW", "FAILED"),
    ("NEEDS_REVIEW", "REFUNDED"),
    ("PENDING_PAYMENT", "NEEDS_REVIEW"),
    ("PAID", "NEEDS_REVIEW"),
    ("IN_QUEUE", "NEEDS_REVIEW"),
    ("IN_PROGRESS", "NEEDS_REVIEW"),
    ("COMPLETED", "NEEDS_REVIEW"),
    ("EXPIRED", "NEEDS_REVIEW"),
    ("PAYMENT_FAILED", "NEEDS_REVIEW"),
    ("FAILED", "NEEDS_REVIEW"),
    ("REFUNDED", "NEEDS_REVIEW"),
]

_TRANSITION_VALUES = ",".join(f"('{a}','{b}')" for a, b in TRANSITIONS)

STATEMENTS = [
    # stock_remaining NULL means unlimited. The CHECK is the last line of defence against oversell.
    """
    CREATE TABLE products (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        sku                text NOT NULL UNIQUE,
        slug               text NOT NULL UNIQUE,
        name               text NOT NULL,
        description_th     text NOT NULL DEFAULT '',
        description_en     text NOT NULL DEFAULT '',
        price_satang       bigint NOT NULL CHECK (price_satang >= 0),
        stock_total        integer CHECK (stock_total >= 0),
        stock_remaining    integer CHECK (stock_remaining >= 0),
        max_qty_per_order  integer CHECK (max_qty_per_order > 0),
        max_qty_per_email  integer CHECK (max_qty_per_email > 0),
        is_active          boolean NOT NULL DEFAULT true,
        created_at         timestamptz NOT NULL DEFAULT now(),
        updated_at         timestamptz NOT NULL DEFAULT now(),
        CHECK ((stock_total IS NULL) = (stock_remaining IS NULL)),
        CHECK (stock_remaining <= stock_total)
    )
    """,
    # Running total per email and product for limits that span several orders.
    """
    CREATE TABLE product_email_usage (
        product_id  uuid NOT NULL REFERENCES products(id),
        email       text NOT NULL CHECK (email = lower(email)),
        qty         integer NOT NULL DEFAULT 0 CHECK (qty >= 0),
        PRIMARY KEY (product_id, email)
    )
    """,
    """
    CREATE TABLE vouchers (
        id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        code                 text NOT NULL UNIQUE,
        kind                 text NOT NULL CHECK (kind IN ('percent','flat')),
        percent_bp           integer NOT NULL DEFAULT 0 CHECK (percent_bp BETWEEN 0 AND 10000),
        max_discount_satang  bigint CHECK (max_discount_satang >= 0),
        flat_satang          bigint NOT NULL DEFAULT 0 CHECK (flat_satang >= 0),
        min_subtotal_satang  bigint NOT NULL DEFAULT 0 CHECK (min_subtotal_satang >= 0),
        max_total_uses       integer CHECK (max_total_uses >= 0),
        used_count           integer NOT NULL DEFAULT 0 CHECK (used_count >= 0),
        max_uses_per_email   integer CHECK (max_uses_per_email > 0),
        is_active            boolean NOT NULL DEFAULT true,
        created_at           timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT voucher_total_cap CHECK (max_total_uses IS NULL OR used_count <= max_total_uses)
    )
    """,
    """
    CREATE TABLE customers (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email       text NOT NULL UNIQUE CHECK (email = lower(email)),
        created_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE orders (
        id                text PRIMARY KEY,
        customer_id       uuid REFERENCES customers(id),
        email             text NOT NULL CHECK (email = lower(email)),
        game_uid          text NOT NULL,
        payment_method    text NOT NULL CHECK (payment_method IN ('promptpay','truemoney','card')),
        voucher_code      text,
        subtotal_satang   bigint NOT NULL CHECK (subtotal_satang >= 0),
        discount_satang   bigint NOT NULL CHECK (discount_satang >= 0),
        net_satang        bigint NOT NULL CHECK (net_satang >= 0),
        fee_satang        bigint NOT NULL CHECK (fee_satang >= 0),
        total_satang      bigint NOT NULL CHECK (total_satang >= 0),
        vat_satang        bigint NOT NULL CHECK (vat_satang >= 0),
        paid_satang       bigint NOT NULL DEFAULT 0 CHECK (paid_satang >= 0),
        refunded_satang   bigint NOT NULL DEFAULT 0 CHECK (refunded_satang >= 0),
        status            text NOT NULL DEFAULT 'PENDING_PAYMENT'
                          CHECK (status IN ({ORDER_STATUSES})),
        holds_inventory   boolean NOT NULL DEFAULT true,
        event_seq         integer NOT NULL DEFAULT 0,
        expires_at        timestamptz NOT NULL,
        created_at        timestamptz NOT NULL DEFAULT now(),
        updated_at        timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT order_net_matches CHECK (net_satang = subtotal_satang - discount_satang),
        CONSTRAINT order_total_matches CHECK (total_satang = net_satang + fee_satang),
        CONSTRAINT order_no_over_refund CHECK (refunded_satang <= paid_satang)
    )
    """,
    "CREATE INDEX orders_created_idx ON orders (created_at DESC, id DESC)",
    "CREATE INDEX orders_status_created_idx ON orders (status, created_at DESC, id DESC)",
    "CREATE INDEX orders_email_created_idx ON orders (email, created_at DESC, id DESC)",
    "CREATE INDEX orders_customer_created_idx ON orders (customer_id, created_at DESC, id DESC)",
    "CREATE INDEX orders_expiry_idx ON orders (expires_at) WHERE status = 'PENDING_PAYMENT'",
    # Prices are copied here, so later catalogue edits cannot change an existing order.
    """
    CREATE TABLE order_items (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        order_id           text NOT NULL REFERENCES orders(id),
        line_no            integer NOT NULL CHECK (line_no > 0),
        product_id         uuid NOT NULL REFERENCES products(id),
        sku                text NOT NULL,
        name               text NOT NULL,
        unit_price_satang  bigint NOT NULL CHECK (unit_price_satang >= 0),
        qty                integer NOT NULL CHECK (qty > 0),
        UNIQUE (order_id, line_no)
    )
    """,
    # Allowed transitions, checked by the trigger below.
    """
    CREATE TABLE order_status_transitions (
        from_status text NOT NULL,
        to_status   text NOT NULL,
        PRIMARY KEY (from_status, to_status)
    )
    """,
    f"INSERT INTO order_status_transitions (from_status, to_status) VALUES {_TRANSITION_VALUES}",
    """
    CREATE FUNCTION orders_guard_transition() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.status IS DISTINCT FROM OLD.status AND NOT EXISTS (
            SELECT 1 FROM order_status_transitions
            WHERE from_status = OLD.status AND to_status = NEW.status
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = 'IB001',
                MESSAGE = 'illegal order transition ' || OLD.status || ' -> ' || NEW.status;
        END IF;
        NEW.updated_at := now();
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER orders_guard_transition_trg BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION orders_guard_transition()
    """,
    # Blocks UPDATE and DELETE. TRUNCATE stays possible for /_test/reset, which only runs in test.
    """
    CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION USING
            ERRCODE = 'IB002',
            MESSAGE = TG_TABLE_NAME || ' is append-only';
    END;
    $$
    """,
    # seq is per order and has no gaps, which is what makes Last-Event-ID replay exact.
    f"""
    CREATE TABLE order_events (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        order_id     text NOT NULL REFERENCES orders(id),
        seq          integer NOT NULL CHECK (seq > 0),
        from_status  text CHECK (from_status IN ({ORDER_STATUSES})),
        to_status    text NOT NULL CHECK (to_status IN ({ORDER_STATUSES})),
        actor        text NOT NULL,
        reason       text NOT NULL,
        metadata     jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        created_at   timestamptz NOT NULL DEFAULT now(),
        UNIQUE (order_id, seq)
    )
    """,
    """
    CREATE TRIGGER order_events_append_only BEFORE UPDATE OR DELETE ON order_events
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation()
    """,
    # The partial unique index enforces the per-email voucher cap under concurrency.
    """
    CREATE TABLE voucher_redemptions (
        id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        voucher_id             uuid NOT NULL REFERENCES vouchers(id),
        order_id               text NOT NULL UNIQUE REFERENCES orders(id),
        email                  text NOT NULL CHECK (email = lower(email)),
        once_per_email         boolean NOT NULL,
        released_at            timestamptz,
        created_at             timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE UNIQUE INDEX voucher_once_per_email ON voucher_redemptions (voucher_id, email)
    WHERE released_at IS NULL AND once_per_email
    """,
    # Primary key on event_id is what makes duplicate webhooks harmless, even simultaneous ones.
    """
    CREATE TABLE webhook_events (
        event_id      text PRIMARY KEY,
        type          text NOT NULL,
        order_id      text,
        amount_satang bigint NOT NULL,
        currency      text NOT NULL,
        occurred_at   timestamptz NOT NULL,
        raw_body      text NOT NULL,
        outcome       text,
        received_at   timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX webhook_events_order_idx ON webhook_events (order_id, received_at)",
    """
    CREATE TABLE refunds (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        order_id           text NOT NULL REFERENCES orders(id),
        amount_satang      bigint NOT NULL CHECK (amount_satang > 0),
        status             text NOT NULL CHECK (status IN ('PENDING','SUCCEEDED','FAILED')),
        reason             text NOT NULL,
        actor              text NOT NULL,
        provider_event_id  text UNIQUE,
        created_at         timestamptz NOT NULL DEFAULT now(),
        updated_at         timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX refunds_order_idx ON refunds (order_id)",
    # Money movement, append-only. This is what gets reconciled against the provider.
    """
    CREATE TABLE ledger_entries (
        id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        order_id           text NOT NULL REFERENCES orders(id),
        kind               text NOT NULL CHECK (kind IN ('payment','refund')),
        amount_satang      bigint NOT NULL CHECK (amount_satang > 0),
        provider_event_id  text UNIQUE,
        refund_id          uuid REFERENCES refunds(id),
        created_at         timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX ledger_order_idx ON ledger_entries (order_id)",
    """
    CREATE TRIGGER ledger_entries_append_only BEFORE UPDATE OR DELETE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation()
    """,
    # One row per order line, with a single stable reference: the anchor of "never top up twice".
    """
    CREATE TABLE fulfillments (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        order_item_id  uuid NOT NULL UNIQUE REFERENCES order_items(id),
        order_id       text NOT NULL REFERENCES orders(id),
        reference      text NOT NULL UNIQUE,
        status         text NOT NULL DEFAULT 'PENDING'
                       CHECK (status IN ('PENDING','SUBMITTING','SUCCEEDED','FAILED','UNKNOWN')),
        attempts       integer NOT NULL DEFAULT 0,
        last_error     text,
        submitted_at   timestamptz,
        confirmed_at   timestamptz,
        created_at     timestamptz NOT NULL DEFAULT now(),
        updated_at     timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX fulfillments_order_idx ON fulfillments (order_id)",
    """
    CREATE TABLE fulfillment_attempts (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        fulfillment_id  uuid NOT NULL REFERENCES fulfillments(id),
        order_id        text NOT NULL REFERENCES orders(id),
        action          text NOT NULL CHECK (action IN ('POST','GET')),
        outcome         text NOT NULL,
        http_status     integer,
        latency_ms      integer,
        detail          text,
        created_at      timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX fulfillment_attempts_order_idx ON fulfillment_attempts (order_id, id)",
    # Job queue in Postgres: a job commits atomically with the state change that created it.
    # status = 'dead' is the dead-letter queue.
    """
    CREATE TABLE jobs (
        id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        kind          text NOT NULL,
        payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
        status        text NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued','running','done','dead')),
        dedupe_key    text,
        run_at        timestamptz NOT NULL DEFAULT now(),
        attempts      integer NOT NULL DEFAULT 0,
        max_attempts  integer NOT NULL DEFAULT 6,
        lease_until   timestamptz,
        locked_by     text,
        last_error    text,
        trace_id      text,
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX jobs_claim_idx ON jobs (run_at, id) WHERE status = 'queued'",
    "CREATE INDEX jobs_lease_idx ON jobs (lease_until) WHERE status = 'running'",
    "CREATE INDEX jobs_dead_idx ON jobs (updated_at) WHERE status = 'dead'",
    """
    CREATE UNIQUE INDEX jobs_dedupe_idx ON jobs (dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('queued','running')
    """,
    # state 'running' reserves the key, so simultaneous duplicates wait instead of double-creating.
    """
    CREATE TABLE idempotency_keys (
        scope          text NOT NULL,
        key            text NOT NULL,
        request_hash   text NOT NULL,
        state          text NOT NULL CHECK (state IN ('running','completed')),
        status_code    integer,
        response_body  bytea,
        created_at     timestamptz NOT NULL DEFAULT now(),
        expires_at     timestamptz NOT NULL,
        PRIMARY KEY (scope, key)
    )
    """,
    "CREATE INDEX idempotency_expiry_idx ON idempotency_keys (expires_at)",
    """
    CREATE TABLE admin_users (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email          text NOT NULL UNIQUE CHECK (email = lower(email)),
        password_hash  text NOT NULL,
        role           text NOT NULL CHECK (role IN ('viewer','support','finance','superadmin')),
        is_active      boolean NOT NULL DEFAULT true,
        created_at     timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE sessions (
        token_hash   text PRIMARY KEY,
        kind         text NOT NULL CHECK (kind IN ('customer','admin')),
        customer_id  uuid REFERENCES customers(id),
        admin_id     uuid REFERENCES admin_users(id),
        csrf_token   text NOT NULL,
        expires_at   timestamptz NOT NULL,
        created_at   timestamptz NOT NULL DEFAULT now(),
        CHECK ((kind = 'customer' AND customer_id IS NOT NULL AND admin_id IS NULL)
            OR (kind = 'admin' AND admin_id IS NOT NULL AND customer_id IS NULL))
    )
    """,
    # One row per email, so requesting a new code cannot clear a lockout.
    """
    CREATE TABLE otp_codes (
        email            text PRIMARY KEY CHECK (email = lower(email)),
        code_hash        text NOT NULL,
        expires_at       timestamptz NOT NULL,
        failed_attempts  integer NOT NULL DEFAULT 0,
        locked_until     timestamptz,
        consumed_at      timestamptz,
        created_at       timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE audit_logs (
        id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        actor       text NOT NULL,
        action      text NOT NULL,
        target      text,
        detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX audit_logs_created_idx ON audit_logs (created_at DESC, id DESC)",
    """
    CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation()
    """,
    """
    CREATE TABLE reconciliation_runs (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        uploaded_by     text NOT NULL,
        file_name       text NOT NULL,
        row_count       integer NOT NULL,
        mismatch_count  integer NOT NULL,
        created_at      timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE reconciliation_items (
        id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id            uuid NOT NULL REFERENCES reconciliation_runs(id),
        category          text NOT NULL CHECK (category IN
                          ('missing_in_system','missing_at_provider','amount_mismatch','status_mismatch')),
        event_id          text,
        order_id          text,
        system_satang     bigint,
        provider_satang   bigint,
        detail            text NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX reconciliation_items_run_idx ON reconciliation_items (run_id, category)",
]


def upgrade() -> None:
    # exec_driver_sql sends each statement untouched, so plpgsql bodies need no escaping.
    connection = op.get_bind()
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError("0001 is the baseline; recreate the database instead")
