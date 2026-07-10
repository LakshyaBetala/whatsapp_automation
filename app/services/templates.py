"""WhatsApp message templates in Hindi / Gujarati / Marathi.

Every message is a Meta **utility** template (₹0.145), pre-approved via AiSensy.
Each entry below pairs:
  - `aisensy_name`: the approved template/campaign name AiSensy sends by, and
  - `body`: a local rendering of the same copy, used for previews, logs, and
    the dev console sender when AiSensy is not configured.

Placeholders use {curly} names. Keep the local body and the approved template
parameter order in sync when you submit templates to Meta.

12 templates × 3 languages = 36. Submit all before launch (24-48h approval).
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


def apply_discount(amount, pct, language: str = "hinglish"):
    """Given an outstanding `amount` and an early-payment discount `pct`,
    return (pay_amount, discount_line).

    pay_amount is what the customer pays today (used for the shown amount and
    the UPI QR); discount_line is the message line to append (empty if pct<=0).
    """
    from decimal import Decimal, ROUND_HALF_UP
    amt = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    try:
        p = Decimal(str(pct or 0))
    except Exception:
        p = Decimal(0)
    if p <= 0:
        return amt, ""
    disc = (amt * p / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    pay = amt - disc
    pstr = f"{float(p):g}"
    if str(language).lower() == "english":
        line = f"Pay today and get {pstr}% off: settle at just {inr(pay)} (you save {inr(disc)})."
    else:
        line = f"Aaj pay karein aur {pstr}% chhoot payein: sirf {inr(pay)} me settle (bachat {inr(disc)})."
    return pay, line


# (template_key, lang) -> {"aisensy_name": str, "body": str}
TEMPLATES: dict[tuple[str, Lang], dict[str, str]] = {
    # --- EOD digest -----------------------------------------------------
    ("eod_digest", Lang.hi): {
        "aisensy_name": "eod_digest_hi",
        "body": (
            "{business}\n"
            "{date} ka hisaab 📋\n\n"
            "Aaj ke naye bills: {bills_count} (total {bills_total})\n"
            "Aaj payment aaya: {payers_count} customers se, {payments_total}\n"
            "Kul baaki: {outstanding_total}\n"
            "Sabse purana pending: {oldest_name}, {oldest_amount} ({oldest_days} din se)\n\n"
            "LIST bhejein: poori baaki list\n"
            "HELP bhejein: saare commands"
        ),
    },
    # --- Invoice delivery ----------------------------------------------
    ("invoice", Lang.hi): {
        "aisensy_name": "invoice_delivery_hi",
        "body": (
            "Namaste {client} ji! 🙏\n"
            "{business} ki taraf se aapka naya bill.\n\n"
            "Bill number: {invoice_number}\n"
            "Amount: {amount}\n"
            "Date: {date}\n\n"
            "UPI se payment: {upi_link}\n\n"
            "Bill ki PDF saath attach hai. Dhanyavaad!"
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
            "Namaste {client} ji,\n"
            "{business} ki taraf se vinamra reminder.\n\n"
            "Bill {invoice_number} ka {outstanding} abhi baaki hai.\n"
            "Suvidha anusaar payment kar dein:\n"
            "{upi_link}\n\n"
            "Payment ho chuka ho to PAID reply karein. Dhanyavaad."
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
    # --- Overdue (firm but respectful, after due date) -------------------
    ("overdue", Lang.hi): {
        "aisensy_name": "overdue_hi",
        "body": (
            "Namaste {client} ji,\n"
            "{business} ka bill {invoice_number} {days_overdue} din se pending hai.\n"
            "Baaki: {outstanding}\n\n"
            "Kripya jald payment kar dein:\n"
            "{upi_link}\n\n"
            "Payment ho gaya ho to PAID reply karein. Dhanyavaad."
        ),
    },
    # --- English variants (msg_language = 'english'); Hinglish is default ---
    ("reminder_en", Lang.hi): {
        "aisensy_name": "reminder_en",
        "body": (
            "Dear {client},\n"
            "A payment reminder from {business}.\n\n"
            "Invoice {invoice_number} of {outstanding} is currently outstanding.\n"
            "Kindly arrange the payment at your convenience:\n"
            "{upi_link}\n\n"
            "If already paid, please reply PAID. Thank you."
        ),
    },
    ("overdue_en", Lang.hi): {
        "aisensy_name": "overdue_en",
        "body": (
            "Dear {client},\n"
            "Invoice {invoice_number} from {business} is now {days_overdue} days overdue.\n"
            "Outstanding: {outstanding}\n\n"
            "Kindly arrange payment at the earliest:\n"
            "{upi_link}\n\n"
            "If already paid, please reply PAID. Thank you."
        ),
    },
    ("reminder_en_gentle", Lang.hi): {
        "aisensy_name": "reminder_en_gentle",
        "body": (
            "Dear {client},\n"
            "A gentle reminder from {business}. No urgency.\n\n"
            "Whenever convenient, kindly review invoice {invoice_number} of {outstanding}:\n"
            "{upi_link}\n\n"
            "If already paid, please reply PAID. Thank you."
        ),
    },
    ("reminder_en_firm", Lang.hi): {
        "aisensy_name": "reminder_en_firm",
        "body": (
            "Dear {client},\n"
            "A payment reminder from {business}.\n\n"
            "Invoice {invoice_number} of {outstanding} is due. Kindly clear it today:\n"
            "{upi_link}\n\n"
            "If already paid, please reply PAID. Thank you."
        ),
    },
    ("overdue_en_gentle", Lang.hi): {
        "aisensy_name": "overdue_en_gentle",
        "body": (
            "Dear {client},\n"
            "Invoice {invoice_number} of {outstanding} from {business} is pending "
            "({days_overdue} days).\n"
            "Whenever you can, kindly clear it:\n"
            "{upi_link}\n\n"
            "Please let us know if there is any issue. If already paid, reply PAID."
        ),
    },
    ("overdue_en_firm", Lang.hi): {
        "aisensy_name": "overdue_en_firm",
        "body": (
            "Dear {client},\n"
            "Invoice {invoice_number} from {business} is now {days_overdue} days overdue.\n"
            "Outstanding: {outstanding}\n\n"
            "Kindly settle it today:\n"
            "{upi_link}\n\n"
            "If already paid, please reply PAID. Thank you."
        ),
    },
    ("invoice_en", Lang.hi): {
        "aisensy_name": "invoice_en",
        "body": (
            "Hello {client},\n"
            "Your new bill from {business}.\n\n"
            "Bill number: {invoice_number}\n"
            "Amount: {amount}\n"
            "Date: {date}\n\n"
            "Pay via UPI: {upi_link}\n\n"
            "The bill PDF is attached. Thank you!"
        ),
    },
    # --- Reminder tone variants (style = gentle | firm; standard = above) ---
    ("reminder_gentle", Lang.hi): {
        "aisensy_name": "reminder_gentle_hi",
        "body": (
            "Namaste {client} ji,\n"
            "{business} ki taraf se ek chhoti si yaad. Koi jaldi nahi.\n\n"
            "Jab suvidha ho, bill {invoice_number} ka {outstanding} dekh lijiyega:\n"
            "{upi_link}\n\n"
            "Payment ho chuka ho to PAID reply karein. Dhanyavaad."
        ),
    },
    ("reminder_firm", Lang.hi): {
        "aisensy_name": "reminder_firm_hi",
        "body": (
            "Namaste {client} ji,\n"
            "{business} ka bill {invoice_number} ka {outstanding} baaki hai.\n\n"
            "Kripya aaj payment kar dein:\n"
            "{upi_link}\n\n"
            "Payment ho gaya ho to PAID reply karein. Dhanyavaad."
        ),
    },
    ("overdue_gentle", Lang.hi): {
        "aisensy_name": "overdue_gentle_hi",
        "body": (
            "Namaste {client} ji,\n"
            "{business} ka bill {invoice_number} ka {outstanding} baaki hai "
            "({days_overdue} din).\n"
            "Jab ho sake, payment kar dijiyega:\n"
            "{upi_link}\n\n"
            "Koi dikkat ho to bata dijiye. Payment ho gaya ho to PAID reply karein."
        ),
    },
    ("overdue_firm", Lang.hi): {
        "aisensy_name": "overdue_firm_hi",
        "body": (
            "Namaste {client} ji,\n"
            "{business} ka bill {invoice_number} ab {days_overdue} din se pending hai.\n"
            "Baaki: {outstanding}\n\n"
            "Kripya aaj hi payment kar dein:\n"
            "{upi_link}\n\n"
            "Payment ho gaya ho to PAID reply karein. Dhanyavaad."
        ),
    },
    # --- Payment confirmation ------------------------------------------
    ("payment_confirmation", Lang.hi): {
        "aisensy_name": "payment_confirmation_hi",
        "body": (
            "Dhanyavaad {client} ji! 🙏\n"
            "Aapka payment {paid_amount} mil gaya hai.\n"
            "Ab baaki: {outstanding}."
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
            "Ek baat {client}, aapke jaisa hi ek aur business bhi yeh system "
            "use karta hai. Automatic bill aur reminder, ₹599/mahine mein. "
            "👉 {website}. Interest ho to HAAN reply karo."
        ),
    },
    ("post_payment_pitch", Lang.gu): {
        "aisensy_name": "post_payment_pitch_gu",
        "body": (
            "Tamne pan aa system gamse {client}, automatic bill ane reminder, "
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


def render(key: str, lang: Lang, style: str = "standard", **params) -> tuple[str, str]:
    """Return (aisensy_template_name, rendered_body).

    ``style`` (gentle | standard | firm) selects a tone variant: a non-standard
    style tries the ``{key}_{style}`` template first. Falls back to the standard
    key, and then to Hindi, so a missing variant never breaks a send.
    """
    # Preference order: correct language beats tone. Try the styled variant in
    # the caller's language, then the base in that language, then fall back to
    # Hindi (styled, then base). So a Gujarati customer on 'gentle' with no gu
    # gentle variant gets the Gujarati base - never Hindi-by-accident.
    styled = style and style != "standard"
    lookups: list[tuple[str, Lang]] = []
    if styled:
        lookups.append((f"{key}_{style}", lang))
    lookups.append((key, lang))
    if styled:
        lookups.append((f"{key}_{style}", Lang.hi))
    lookups.append((key, Lang.hi))

    entry = next((TEMPLATES[k] for k in lookups if k in TEMPLATES), None)
    if entry is None:
        raise KeyError(f"No template for key={key!r}")
    try:
        body = entry["body"].format(**params)
    except KeyError as exc:
        raise KeyError(f"Missing template param {exc} for {key}/{lang}") from exc
    return entry["aisensy_name"], body
