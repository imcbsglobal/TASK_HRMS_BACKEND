"""
watsapp_config/notify.py
------------------------
High-level WhatsApp notification dispatcher.

Usage (from attendance/views.py or any other app):

    from watsapp_config.notify import send_notification

    send_notification(
        admin_owner  = user.admin_owner,   # the tenant's admin User object
        purpose_key  = 'punch_in',         # key matching WhatsAppNotificationPurpose
        employee_user = user,              # the employee User whose action triggered the event
        context      = {                   # substitution variables for the message template
            'name':  'John Doe',
            'time':  '09:15 AM',
            'date':  '2025-06-04',
        },
    )

Everything is fire-and-forget.  Failures are logged but never raised.
"""

import logging
import threading

from .utils import send_whatsapp_message

logger = logging.getLogger(__name__)

# ── Default message templates per purpose ────────────────────────────────────
# Use {variable} placeholders that match the 'context' dict passed in.
# Keep messages short — WhatsApp is a mobile channel.

DEFAULT_TEMPLATES = {
    'punch_in': (
        "✅ *Check-In Alert*\n"
        "👤 Employee: {name}\n"
        "🕐 Time: {time}\n"
        "📅 Date: {date}"
    ),
    'punch_out': (
        "🔴 *Check-Out Alert*\n"
        "👤 Employee: {name}\n"
        "🕐 Time: {time}\n"
        "⏱ Total Hours: {total_hours}\n"
        "📅 Date: {date}"
    ),
    'late_checkin': (
        "⏰ *Late Check-In Alert*\n"
        "👤 {name} arrived late.\n"
        "🕐 Check-in at: {time}\n"
        "📅 Date: {date}"
    ),
    'leave_request': (
        "📅 *New Leave Request*\n"
        "👤 {name} has requested leave.\n"
        "📋 Type: {leave_type}\n"
        "🗓 From: {start_date}  To: {end_date}\n"
        "📝 Reason: {reason}"
    ),
    'leave_approved': (
        "✅ *Leave Approved*\n"
        "Dear {name}, your leave request has been *approved*.\n"
        "📋 Type: {leave_type}\n"
        "🗓 From: {start_date}  To: {end_date}"
    ),
    'leave_rejected': (
        "❌ *Leave Rejected*\n"
        "Dear {name}, your leave request has been *rejected*.\n"
        "📋 Type: {leave_type}\n"
        "🗓 From: {start_date}  To: {end_date}\n"
        "📝 Admin notes: {admin_notes}"
    ),
    'late_request': (
        "🙏 *Late Arrival Request*\n"
        "👤 {name} has submitted a late arrival request.\n"
        "🕐 Expected arrival: {expected_time}\n"
        "📅 Date: {date}\n"
        "📝 Reason: {reason}"
    ),
    'late_approved': (
        "✅ *Late Arrival Approved*\n"
        "Dear {name}, your late arrival request for {date} has been *approved*."
    ),
    'late_rejected': (
        "❌ *Late Arrival Rejected*\n"
        "Dear {name}, your late arrival request for {date} has been *rejected*.\n"
        "📝 Admin notes: {admin_notes}"
    ),
    'early_departure_request': (
        "🚪 *Early Departure Request*\n"
        "👤 {name} has requested early departure.\n"
        "🕐 Expected departure: {expected_time}\n"
        "📅 Date: {date}\n"
        "📝 Reason: {reason}"
    ),
    'early_departure_approved': (
        "✅ *Early Departure Approved*\n"
        "Dear {name}, your early departure request for {date} has been *approved*."
    ),
    'early_departure_rejected': (
        "❌ *Early Departure Rejected*\n"
        "Dear {name}, your early departure request for {date} has been *rejected*.\n"
        "📝 Admin notes: {admin_notes}"
    ),
    'salary_advance_request': (
        "💵 *Salary Advance Request*\n"
        "👤 {name} has requested a salary advance.\n"
        "💰 Amount: {amount}\n"
        "🔄 Repayment: {repayment_months} month(s)\n"
        "📝 Reason: {reason}"
    ),
    'salary_advance_approved': (
        "✅ *Salary Advance Approved*\n"
        "Dear {name}, your salary advance request has been *approved*.\n"
        "💰 Approved Amount: {approved_amount}\n"
        "🔄 Repayment: {repayment_months} month(s)"
    ),
    'salary_advance_rejected': (
        "❌ *Salary Advance Rejected*\n"
        "Dear {name}, your salary advance request has been *rejected*.\n"
        "📝 Admin notes: {admin_notes}"
    ),
    'payslip': (
        "💰 *Payslip Generated*\n"
        "Dear {name}, your payslip for {month} is ready.\n"
        "💵 Net Pay: {net_pay}\n"
        "Please log in to HRMS to download it."
    ),
    'announcement': (
        "📢 *Company Announcement*\n"
        "{title}\n\n"
        "{body}"
    ),
    'overtime': (
        "🕐 *Overtime Alert*\n"
        "👤 {name} has been working overtime today.\n"
        "⏱ Hours so far: {hours}\n"
        "📅 Date: {date}"
    ),
    'absent': (
        "🚨 *Absent Alert*\n"
        "👤 {name} is absent today.\n"
        "📅 Date: {date}"
    ),
    'wfh_request': (
        "🏠 *Work From Home Request*\n"
        "👤 {name} has requested to work from home.\n"
        "📅 Date: {date}\n"
        "📝 Reason: {reason}"
    ),
    'wfh_approved': (
        "✅ *Work From Home Approved*\n"
        "Dear {name}, your work from home request has been *approved*.\n"
        "📅 Date: {date}"
    ),
    'wfh_rejected': (
        "❌ *Work From Home Rejected*\n"
        "Dear {name}, your work from home request has been *rejected*.\n"
        "📅 Date: {date}\n"
        "📝 Admin notes: {admin_notes}"
    ),
}

# ── Per-purpose send defaults (used when no DB row exists yet) ────────────────
# send_to_employee: whether to message the employee whose action triggered the event
# send_to_admin:    whether to message subscribed admin numbers
PURPOSE_DEFAULTS = {
    # Punch events — admin needs to see these too
    'punch_in':                  {'send_to_employee': True,  'send_to_admin': True},
    'punch_out':                 {'send_to_employee': True,  'send_to_admin': True},
    # Late / leave — employee confirmation + admin visibility
    'late_checkin':              {'send_to_employee': False, 'send_to_admin': True},
    'late_request':              {'send_to_employee': False, 'send_to_admin': True},
    'late_approved':             {'send_to_employee': True,  'send_to_admin': False},
    'late_rejected':             {'send_to_employee': True,  'send_to_admin': False},
    'leave_request':             {'send_to_employee': False, 'send_to_admin': True},
    'leave_approved':            {'send_to_employee': True,  'send_to_admin': False},
    'leave_rejected':            {'send_to_employee': True,  'send_to_admin': False},
    # Early departure
    'early_departure_request':   {'send_to_employee': False, 'send_to_admin': True},
    'early_departure_approved':  {'send_to_employee': True,  'send_to_admin': False},
    'early_departure_rejected':  {'send_to_employee': True,  'send_to_admin': False},
    # Salary advance — employee confirmation + admin awareness
    'salary_advance_request':    {'send_to_employee': False, 'send_to_admin': True},
    'salary_advance_approved':   {'send_to_employee': True,  'send_to_admin': False},
    'salary_advance_rejected':   {'send_to_employee': True,  'send_to_admin': False},
    # WFH — admin gets request, employee gets result
    'wfh_request':               {'send_to_employee': False, 'send_to_admin': True},
    'wfh_approved':              {'send_to_employee': True,  'send_to_admin': False},
    'wfh_rejected':              {'send_to_employee': True,  'send_to_admin': False},
    # Others default to employee-only (defined below as fallback)
}


def _get_employee_phone(employee_user) -> str | None:
    """
    Try to find a WhatsApp-capable phone number for the employee.
    First checks Employee profile (employee_management.Employee),
    then falls back to the User model's phone field if present.
    """
    try:
        from employee_management.models import Employee
        emp = Employee.objects.filter(admin_owner=employee_user.admin_owner).filter(
            email=employee_user.email
        ).first()
        if emp and emp.phone:
            return emp.phone.strip()
    except Exception:  # noqa: BLE001
        pass

    # Fallback: some custom User models store phone directly
    phone = getattr(employee_user, 'phone', None) or getattr(employee_user, 'mobile', None)
    return str(phone).strip() if phone else None


def _build_message(purpose_key: str, context: dict) -> str:
    """Render the template for *purpose_key* with *context* substitution.
    Missing placeholders are left as-is rather than raising KeyError."""
    template = DEFAULT_TEMPLATES.get(purpose_key, "📩 HRMS Notification ({purpose_key})")
    # Use a defaultdict so any missing key is kept as a visible placeholder
    from collections import defaultdict
    safe_ctx = defaultdict(lambda: '—', {**context, 'purpose_key': purpose_key})
    return template.format_map(safe_ctx)


def _dispatch(admin_owner, purpose_key: str, employee_user, context: dict):
    """
    Core dispatcher (runs in a background thread).

    Behaviour:
    - If a WhatsAppNotificationPurpose row exists AND enabled=False  → skip silently.
    - If a row exists AND enabled=True                               → honour send_to_employee / send_to_admin flags.
    - If NO row exists at all                                        → default to send_to_employee=True,
                                                                       so events fire even before the admin
                                                                       visits the Notification Setup tab.
    """
    from .models import WhatsAppNotificationPurpose, WhatsAppAdminNumber

    try:
        purpose = WhatsAppNotificationPurpose.objects.filter(
            admin_owner=admin_owner,
            key=purpose_key,
        ).first()

        # Explicitly disabled by the admin — respect that and stop here.
        if purpose is not None and not purpose.enabled:
            return

        # Determine send flags: use DB values if a row exists, else PURPOSE_DEFAULTS,
        # else fall back to employee-only.
        _def = PURPOSE_DEFAULTS.get(purpose_key, {'send_to_employee': True, 'send_to_admin': False})
        send_to_employee = purpose.send_to_employee if purpose is not None else _def['send_to_employee']
        send_to_admin    = purpose.send_to_admin    if purpose is not None else _def['send_to_admin']

        message = _build_message(purpose_key, context)

        # ── Send to employee ──────────────────────────────────────────────────
        if send_to_employee and employee_user:
            phone = _get_employee_phone(employee_user)
            if phone:
                ok, err = send_whatsapp_message(admin_owner, phone, message)
                if not ok:
                    logger.warning(
                        "WA notify [%s] → employee %s (%s): %s",
                        purpose_key, employee_user.pk, phone, err,
                    )
            else:
                logger.debug(
                    "WA notify [%s]: no phone for employee %s — skipped",
                    purpose_key, employee_user.pk,
                )

        # ── Send to admin numbers ─────────────────────────────────────────────
        if send_to_admin:
            admin_nums = WhatsAppAdminNumber.objects.filter(
                admin_owner=admin_owner,
                active=True,
            )
            for admin_num in admin_nums:
                if not admin_num.phone:
                    continue

                # If the admin number has NO purposes configured at all, send
                # everything (act as a catch-all number).
                # If purposes ARE configured, only send for subscribed keys.
                subscribed = admin_num.purposes or []
                if subscribed and purpose_key not in subscribed:
                    continue

                ok, err = send_whatsapp_message(admin_owner, admin_num.phone, message)
                if not ok:
                    logger.warning(
                        "WA notify [%s] → admin '%s' (%s): %s",
                        purpose_key, admin_num.name, admin_num.phone, err,
                    )

    except Exception as exc:  # noqa: BLE001
        logger.exception("WA notify [%s] unexpected error: %s", purpose_key, exc)


def send_notification(admin_owner, purpose_key: str, employee_user=None, context: dict = None):
    """
    Fire-and-forget WhatsApp notification.

    Runs in a daemon thread so it never blocks the HTTP response.

    Args:
        admin_owner:    The tenant's admin User (or None for SUPER_ADMIN scope).
        purpose_key:    One of the keys defined in DEFAULT_TEMPLATES / ALL_PURPOSES.
        employee_user:  The employee User object (used to look up their phone).
        context:        Dict of substitution variables for the message template.
    """
    if admin_owner is None:
        return  # SUPER_ADMIN actions don't map to a single tenant config

    t = threading.Thread(
        target=_dispatch,
        args=(admin_owner, purpose_key, employee_user, context or {}),
        daemon=True,
    )
    t.start()
