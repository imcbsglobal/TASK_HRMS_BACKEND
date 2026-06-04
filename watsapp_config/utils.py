"""
watsapp_config/utils.py
-----------------------
Low-level WhatsApp message sender.

Usage:
    from watsapp_config.utils import send_whatsapp_message

    ok, err = send_whatsapp_message(
        admin_owner=user.admin_owner,
        phone="919876543210",        # E.164 without the '+', or with it
        message="Hello from HRMS!",
    )
    if not ok:
        logger.warning("WhatsApp send failed: %s", err)

Returns:
    (True, None)          – message sent successfully
    (False, error_string) – something went wrong
"""

import logging
import requests

logger = logging.getLogger(__name__)


def _normalise_phone(phone: str) -> str:
    """Strip spaces, dashes, and a leading '+' so we get a plain digit string."""
    return phone.strip().lstrip('+').replace(' ', '').replace('-', '')


def send_whatsapp_message(admin_owner, phone: str, message: str) -> tuple[bool, str | None]:
    """
    Look up the WhatsApp config for *admin_owner*, pick the right provider,
    and send *message* to *phone*.

    Returns (True, None) on success, (False, reason) on failure.
    Failures are intentionally non-fatal — callers should log but not crash.
    """
    from .models import WhatsAppConfig  # local import to avoid circular imports

    if not phone or not message:
        return False, "Phone or message is empty."

    phone = _normalise_phone(phone)
    if not phone:
        return False, "Phone number is blank after normalisation."

    # ── Fetch tenant config ───────────────────────────────────────────────────
    cfg = WhatsAppConfig.objects.filter(admin_owner=admin_owner, is_active=True).first()
    if not cfg:
        return False, "WhatsApp not configured or not active for this tenant."

    provider    = cfg.provider
    instance_id = cfg.instance_id.strip()
    api_token   = cfg.api_token.strip()

    if not instance_id or not api_token:
        return False, "WhatsApp credentials are incomplete."

    # ── Route to the correct provider ────────────────────────────────────────
    try:
        if provider == 'dxing':
            api_url = cfg.webhook_url.strip() if cfg.webhook_url else 'https://app.dxing.in/api/send/whatsapp'
            return _send_dxing(instance_id, api_token, phone, message, api_url=api_url)
        elif provider == 'ultramsg':
            return _send_ultramsg(instance_id, api_token, phone, message)
        elif provider == 'waapi':
            return _send_waapi(instance_id, api_token, phone, message)
        elif provider == 'twilio':
            sender = cfg.phone_number.strip() if cfg.phone_number else ''
            return _send_twilio(instance_id, api_token, sender, phone, message)
        elif provider == 'meta':
            return _send_meta(instance_id, api_token, phone, message)
        elif provider == 'wablas':
            return _send_wablas(instance_id, api_token, phone, message)
        elif provider == 'custom':
            webhook_url = cfg.webhook_url.strip() if cfg.webhook_url else ''
            return _send_custom(webhook_url, instance_id, api_token, phone, message)
        else:
            return False, f"Unknown provider '{provider}'."

    except requests.exceptions.ConnectionError as exc:
        logger.warning("WhatsApp connection error (%s): %s", provider, exc)
        return False, f"Could not reach {provider} API."
    except requests.exceptions.Timeout as exc:
        logger.warning("WhatsApp timeout (%s): %s", provider, exc)
        return False, f"{provider} API timed out."
    except Exception as exc:  # noqa: BLE001
        logger.exception("WhatsApp unexpected error (%s): %s", provider, exc)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Provider-specific senders
# ─────────────────────────────────────────────────────────────────────────────

def _send_dxing(account: str, secret: str, phone: str, message: str, api_url: str = 'https://app.dxing.in/api/send/whatsapp') -> tuple[bool, str | None]:
    """
    DXing WhatsApp API
    GET {api_url}?secret=<api_token>&account=<instance_id>&recipient=<phone>&type=text&message=<msg>&priority=1
    """
    resp = requests.get(
        api_url,
        params={
            'secret':    secret,
            'account':   account,
            'recipient': phone,
            'type':      'text',
            'message':   message,
            'priority':  '1',
        },
        timeout=15,
    )
    if resp.status_code == 200:
        data = resp.json() if resp.content else {}
        # DXing returns {"status": "success"} on success
        if str(data.get('status', '')).lower() in ('success', 'true', '1', 'ok', '200') or resp.status_code == 200:
            return True, None
    return False, f"DXing returned {resp.status_code}: {resp.text[:200]}"


def _send_ultramsg(instance_id: str, token: str, phone: str, message: str) -> tuple[bool, str | None]:
    """UltraMsg — POST /messages/chat"""
    url = f"https://api.ultramsg.com/{instance_id}/messages/chat"
    resp = requests.post(
        url,
        data={'token': token, 'to': phone, 'body': message},
        timeout=15,
    )
    if resp.status_code == 200:
        return True, None
    return False, f"UltraMsg returned {resp.status_code}: {resp.text[:200]}"


def _send_waapi(instance_id: str, api_key: str, phone: str, message: str) -> tuple[bool, str | None]:
    """WaAPI — POST /instances/{id}/client/action/send-message"""
    url = f"https://waapi.app/api/v1/instances/{instance_id}/client/action/send-message"
    resp = requests.post(
        url,
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={'chatId': f'{phone}@c.us', 'message': message},
        timeout=15,
    )
    if resp.status_code in (200, 201):
        return True, None
    return False, f"WaAPI returned {resp.status_code}: {resp.text[:200]}"


def _send_twilio(account_sid: str, auth_token: str, sender: str, phone: str, message: str) -> tuple[bool, str | None]:
    """
    Twilio — POST /Accounts/{SID}/Messages.json
    Sender must be in whatsapp:+NNNN format.
    """
    if not sender:
        return False, "Twilio sender WhatsApp number is not configured."

    # Ensure sender has the whatsapp: prefix
    if not sender.startswith('whatsapp:'):
        sender = f'whatsapp:{sender}'

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    resp = requests.post(
        url,
        auth=(account_sid, auth_token),
        data={
            'From': sender,
            'To':   f'whatsapp:+{phone}',
            'Body': message,
        },
        timeout=15,
    )
    if resp.status_code in (200, 201):
        return True, None
    return False, f"Twilio returned {resp.status_code}: {resp.text[:200]}"


def _send_meta(phone_number_id: str, access_token: str, phone: str, message: str) -> tuple[bool, str | None]:
    """Meta Cloud API — POST /v18.0/{phone_number_id}/messages"""
    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    resp = requests.post(
        url,
        headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
        json={
            'messaging_product': 'whatsapp',
            'to': phone,
            'type': 'text',
            'text': {'body': message},
        },
        timeout=15,
    )
    if resp.status_code in (200, 201):
        return True, None
    return False, f"Meta returned {resp.status_code}: {resp.text[:200]}"


def _send_wablas(domain: str, token: str, phone: str, message: str) -> tuple[bool, str | None]:
    """Wablas — POST {domain}/api/send-message"""
    url = f"{domain.rstrip('/')}/api/send-message"
    resp = requests.post(
        url,
        headers={'Authorization': token, 'Content-Type': 'application/json'},
        json={'phone': phone, 'message': message},
        timeout=15,
    )
    if resp.status_code in (200, 201):
        return True, None
    return False, f"Wablas returned {resp.status_code}: {resp.text[:200]}"


def _send_custom(webhook_url: str, instance_id: str, api_key: str, phone: str, message: str) -> tuple[bool, str | None]:
    """
    Generic custom provider — POST to webhook_url with a JSON body.
    Passes all credentials so the provider can use whichever it needs.
    """
    if not webhook_url:
        return False, "Custom provider API URL is not configured."

    resp = requests.post(
        webhook_url,
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={
            'instance_id': instance_id,
            'api_key':     api_key,
            'to':          phone,
            'message':     message,
        },
        timeout=15,
    )
    if resp.status_code in (200, 201):
        return True, None
    return False, f"Custom provider returned {resp.status_code}: {resp.text[:200]}"
