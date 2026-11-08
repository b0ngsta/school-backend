"""Per-school feature flags: the catalogue (migration 0019_features).

A flag is what EduManage HQ (the owner portal, work/dashboard) switches on or
off for one school. Off = the module is gone for every user of that school:
the API answers 403 `{"code": "feature_disabled", "feature": key}` on its
endpoints, and the web / mobile apps hide its menu entries (they read the
flags from /auth/me and the token responses).

* Keys are stored in the database and in the apps: never rename one.
* Every feature defaults to ON, so a school without rows in
  `organisation_features` works exactly as before flags existed.
* Switching a feature off never deletes data; switching it back on shows
  everything as it was.

Adding a feature: add it here, gate its router(s) with
`Depends(require_feature("<key>"))` (app/dependencies.py), handle side effects
(dashboards, /updates, jobs), and add `feature: '<key>'` to the menus of the
web app (App.jsx NAV) and the mobile app (launchers + SCREENS). HQ draws the
list from GET /platform/features, so it needs no change.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Feature:
    key: str
    label: str          # shown in HQ and in the 403 message
    description: str    # one line for HQ
    default: bool = True

    def public(self) -> dict:
        return asdict(self)


CATALOGUE: tuple[Feature, ...] = (
    Feature("attendance", "Attendance",
            "Students' daily attendance: marking by teachers, reports, and the parents' Attendance screen."),
    Feature("fees", "Fees",
            "Fee records, payments, payment proofs (fee claims), reminders and the parents' Fees screen."),
    Feature("transport", "Transport",
            "Vehicles, riders, the driver's My Routine and the parents' Transport screen."),
    Feature("lesson_plans", "Lesson plans", "Teachers' lesson plans with files."),
    Feature("salary", "Salary", "Staff salary records and the staff's My Salary screen."),
)
FEATURES: dict[str, Feature] = {f.key: f for f in CATALOGUE}
DEFAULTS: dict[str, bool] = {f.key: f.default for f in CATALOGUE}
