"""Daily reminder job (run it at ~08:00 school time, see POST /internal/jobs/fee-reminders).

* fees due within the next 3 days -> "due soon"
* fees that became overdue (due date passed in the last 7 days, so a missed
  run catches up) -> "overdue"

`fee_reminders_sent` records every reminder, so running the job twice (or
every hour) never sends anything twice.
"""
from __future__ import annotations

from datetime import timedelta

from app.core.clock import school_today
from app.db import Db
from app.services import notify

DUE_SOON_DAYS = 3
OVERDUE_CATCH_UP_DAYS = 7


def send_fee_reminders(db: Db) -> dict:
    today = school_today()
    sent = {"due_soon": 0, "overdue": 0}
    with db.transaction():
        due_soon = db.all(
            "SELECT f.id, f.student_id, f.title, f.amount, f.due_date FROM fees f "
            "JOIN users u ON u.id = f.student_id AND u.is_active = 1 "
            "WHERE f.status = 'pending' AND f.due_date BETWEEN %s AND %s "
            "AND NOT EXISTS (SELECT 1 FROM fee_reminders_sent r WHERE r.fee_id = f.id AND r.kind = 'due_soon') "
            "FOR UPDATE",
            (today, today + timedelta(days=DUE_SOON_DAYS)),
        )
        overdue = db.all(
            "SELECT f.id, f.student_id, f.title, f.amount, f.due_date FROM fees f "
            "JOIN users u ON u.id = f.student_id AND u.is_active = 1 "
            "WHERE f.status = 'pending' AND f.due_date < %s AND f.due_date >= %s "
            "AND NOT EXISTS (SELECT 1 FROM fee_reminders_sent r WHERE r.fee_id = f.id AND r.kind = 'overdue') "
            "FOR UPDATE",
            (today, today - timedelta(days=OVERDUE_CATCH_UP_DAYS)),
        )
        for kind, fees in (("due_soon", due_soon), ("overdue", overdue)):
            if not fees:
                continue
            # INSERT IGNORE: a parallel run that got there first keeps its row and we skip
            db.execute_many("INSERT IGNORE INTO fee_reminders_sent (fee_id, kind) VALUES (%s, %s)",
                            [(f["id"], kind) for f in fees])
            items = []
            for f in fees:
                amount, due = notify.inr(f["amount"]), notify.day_month(f["due_date"])
                if kind == "due_soon":
                    title = f"Fee due soon: {f['title']} — {amount} due {due}"
                else:
                    title = f"Fee overdue: {f['title']} — {amount} was due {due}"
                items.append(notify.Item(f["student_id"], title, None, notify.route("fee", student=True)))
            notify.notify_each(db, items, "fee")
            sent[kind] = len(items)
    return sent
