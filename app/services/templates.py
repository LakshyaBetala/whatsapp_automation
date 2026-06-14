"""WhatsApp message templates in Hindi / Gujarati / Marathi.

Every message is a Meta **utility** template (₹0.145), pre-approved via AiSensy.
Each entry below pairs:
  - `aisensy_name`: the approved template/campaign name AiSensy sends by, and
  - `body`: a local rendering of the same copy, used for previews, logs, and
    the dev console sender when AiSensy is not configured.

Placeholders use {curly} names. Keep the local body and the approved template
parameter order in sync when you submit templates to Meta.

12 templates × 3 languages = 36. Submit all before launch (24–48h approval).
"""
from __future__ import annotations

from app.models import Lang


def inr(amount) -> str:
    """Format a number as Indian-grouped rupees: 843000 -> '8,43,000'."""
    try:
        n = int(round(float(amount)))
    except (TypeError, ValueError):
        return str(amount)
    s = str(abs(n))
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        s = ",".join(parts) + "," + last3
    return ("-" if n < 0 else "") + "₹" + s


# (template_key, lang) -> {"aisensy_name": str, "body": str}
TEMPLATES: dict[tuple[str, Lang], dict[str, str]] = {
    # --- EOD digest -----------------------------------------------------
    ("eod_digest", Lang.hi): {
        "aisensy_name": "eod_digest_hi",
        "body": (
            "{business} — {date} ka summary\n\n"
            "Aaj ke bills: {bills_count}  |  Total: {bills_total}\n"
            "Payment aaya: {payers_count} customers — {payments_total}\n"
            "Kul outstanding: {outstanding_total}\n"
            "Sabse purana: {oldest_name} — {oldest_amount} ({oldest_days} din)\n\n"
            "Reply LIST — poori list\n"
            "Reply STOP [naam] — reminders band"
        ),
    },
    # --- Invoice delivery ----------------------------------------------
    ("invoice", Lang.hi): {
        "aisensy_name": "invoice_delivery_hi",
        "body": (
            "Namaste {client}! {business} se aapka bill.\n\n"
            "Bill no: {invoice_number}\n"
            "Amount: {amount}\n"
            "Date: {date}\n\n"
            "Payment ke liye: {upi_link}\n"
            "Bill PDF: {pdf_url}"
        ),
    },
    ("invoice", Lang.gu): {
        "aisensy_name": "invoice_delivery_gu",
        "body": (
            "Namaste {client}! {business} taraf thi tamaru bill.\n\n"
            "Bill no: {invoice_number}\n"
            "Rakam: {amount}\n"
            "Taarikh: {date}\n\n"
            "Payment mate: {upi_link}\n"
            "Bill PDF: {pdf_url}"
        ),
    },
    ("invoice", Lang.mr): {
        "aisensy_name": "invoice_delivery_mr",
        "body": (
            "Namaskar {client}! {business} kadun tumche bill.\n\n"
            "Bill no: {invoice_number}\n"
            "Rakkam: {amount}\n"
            "Dinank: {date}\n\n"
            "Payment sathi: {upi_link}\n"
            "Bill PDF: {pdf_url}"
        ),
    },
    # --- Reminders (one approved template per cadence day) --------------
    ("reminder", Lang.hi): {
        "aisensy_name": "reminder_hi",
        "body": (
            "Namaste {client}, {business} se yaad dilana.\n\n"
            "Bill no {invoice_number} ka {outstanding} baaki hai "
            "({days_overdue} din ho gaye).\n"
            "Payment ke liye: {upi_link}\n\n"
            "Payment ho gaya ho to PAID reply karein."
        ),
    },
    ("reminder", Lang.gu): {
        "aisensy_name": "reminder_gu",
        "body": (
            "Namaste {client}, {business} taraf thi yaad.\n\n"
            "Bill no {invoice_number} nu {outstanding} baaki chhe "
            "({days_overdue} divas thaya).\n"
            "Payment mate: {upi_link}\n\n"
            "Payment thai gayu hoy to PAID reply karo."
        ),
    },
    ("reminder", Lang.mr): {
        "aisensy_name": "reminder_mr",
        "body": (
            "Namaskar {client}, {business} kadun athvan.\n\n"
            "Bill no {invoice_number} che {outstanding} baki aahe "
            "({days_overdue} divas zaale).\n"
            "Payment sathi: {upi_link}\n\n"
            "Payment zhaala asel tar PAID reply kara."
        ),
    },
    # --- Payment confirmation ------------------------------------------
    ("payment_confirmation", Lang.hi): {
        "aisensy_name": "payment_confirmation_hi",
        "body": (
            "Payment mil gaya {client}, shukriya! 🙏\n"
            "Mila: {paid_amount}. Baaki: {outstanding}."
        ),
    },
    ("payment_confirmation", Lang.gu): {
        "aisensy_name": "payment_confirmation_gu",
        "body": (
            "Payment maḷyo {client}, dhanyavaad! 🙏\n"
            "Maḷyu: {paid_amount}. Baaki: {outstanding}."
        ),
    },
    ("payment_confirmation", Lang.mr): {
        "aisensy_name": "payment_confirmation_mr",
        "body": (
            "Payment milale {client}, dhanyavaad! 🙏\n"
            "Milale: {paid_amount}. Baki: {outstanding}."
        ),
    },
    # --- Post-payment pitch --------------------------------------------
    ("post_payment_pitch", Lang.hi): {
        "aisensy_name": "post_payment_pitch_hi",
        "body": (
            "Ek baat {client} — aapke jaisa hi ek aur business bhi yeh system "
            "use karta hai. Automatic bill aur reminder, ₹599/mahine mein. "
            "👉 {website}. Interest ho to HAAN reply karo."
        ),
    },
    ("post_payment_pitch", Lang.gu): {
        "aisensy_name": "post_payment_pitch_gu",
        "body": (
            "Tamne pan aa system gamse {client} — automatic bill ane reminder, "
            "₹599/mahine. 👉 {website}. Interest hoy to HA reply karo."
        ),
    },
    # --- Owner alert (new HAAN lead, day-45 escalation, payment) --------
    ("owner_alert", Lang.hi): {
        "aisensy_name": "owner_alert_hi",
        "body": "{business}: {alert}",
    },
    # --- Low stock ------------------------------------------------------
    ("low_stock", Lang.hi): {
        "aisensy_name": "low_stock_hi",
        "body": "Stock alert: {item} sirf {qty} bacha hai (threshold {threshold}).",
    },
    # --- Monthly P&L ----------------------------------------------------
    ("monthly_pnl", Lang.hi): {
        "aisensy_name": "monthly_pnl_hi",
        "body": "{month} mein net income: {net_income}.",
    },
    # --- New customer welcome ------------------------------------------
    ("welcome", Lang.hi): {
        "aisensy_name": "welcome_hi",
        "body": (
            "Namaste! Aap {business} ke customer ban gaye hain. "
            "Credit limit: {credit_limit}."
        ),
    },
}


def render(key: str, lang: Lang, **params) -> tuple[str, str]:
    """Return (aisensy_template_name, rendered_body).

    Falls back to Hindi if a (key, lang) pair has not been authored yet, so a
    missing Marathi variant never breaks a send during rollout.
    """
    entry = TEMPLATES.get((key, lang)) or TEMPLATES.get((key, Lang.hi))
    if entry is None:
        raise KeyError(f"No template for key={key!r}")
    try:
        body = entry["body"].format(**params)
    except KeyError as exc:
        raise KeyError(f"Missing template param {exc} for {key}/{lang}") from exc
    return entry["aisensy_name"], body
