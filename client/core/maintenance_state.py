"""
maintenance_state.py

Thin QDateTime<->API adapter over GET/PATCH /maintenance -- shared
between the admin-side MaintenanceTab (writes) and the user-side
MaintenanceScreen (reads). Used to wrap data/db.py's MaintenanceState
table directly; now goes through core/api_client.py instead (Phase 5 --
no local DB access left in the desktop app). Same function
names/signatures/return shape as before, so neither caller needed to
change.
"""

from PySide6.QtCore import QDateTime, Qt

from core.api_client import (
    get_maintenance_state as _get,
    update_maintenance_state as _update,
)


_DATETIME_FIELDS = ("start_datetime", "end_datetime")


def get_maintenance_state() -> dict:
    """Same shape callers used to get from the old dict, but
    start_datetime/end_datetime come back as QDateTime instead of
    raw strings."""
    row = _get()
    for field in _DATETIME_FIELDS:
        row[field] = QDateTime.fromString(row[field], Qt.ISODate) if row[field] else None
    return row


def update_maintenance_state(**fields) -> dict:
    for field in _DATETIME_FIELDS:
        if field in fields and isinstance(fields[field], QDateTime):
            fields[field] = fields[field].toString(Qt.ISODate)
    return _update(**fields)