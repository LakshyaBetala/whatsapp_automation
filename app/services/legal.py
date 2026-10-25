"""What to do once WhatsApp has run out of road.

The reminder cadence stops at 300 days past due (OVERDUE_WINDOW_DAYS). After
that another message is not the instrument the debt needs, and the screens used
to say "escalation required" on every such party - 74 of them in one shop, all
looking equally urgent.

They are not equally urgent. A money claim for the price of goods sold runs out
roughly three years after the money became due (Limitation Act 1963); after
that no notice, portal or court revives it. So the axis that decides what to do
is how much RUNWAY is left, not how many days are overdue - that is the only
clock in the whole product with a hard deadline on it.

Four states, in the order the owner should care about them:

  act_now     something expires within six months. Do this quarter.
  notice      past the cadence, still well in time. Worth a demand notice.
  small       past the cadence, in time, but too little to spend a notice on.
  time_barred past three years. The money is gone; clear it out of the books.

Sections 18 and 19 of the Limitation Act restart the three years - a signed
written acknowledgment, or a part payment - but only if they land BEFORE the
period expires. That is why a part payment of any size is the cheapest lever on
an act_now debt: collecting Rs 500 buys three more years. Whether a WhatsApp
reply admitting a debt counts as a signed writing under s.18 is unsettled, so
nothing here relies on it.

Pure functions plus two text builders. No I/O, no database, no sending. ASVA
drafts; the owner's advocate signs and serves.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal, ROUND_HALF_UP

from app.jobs.reminder_sweep import OVERDUE_WINDOW_DAYS

# Three years. Kept as days so every comparison in here is integer arithmetic
# on dates; leap years move the real deadline by a day or two, which is why
# every label says "act before <date>" and not "you have exactly N days".
LIMITATION_DAYS = 3 * 365

# Under six months of runway = a notice has to go out this quarter for service,
# a reply and a filing to all fit before the claim dies.
URGENT_DAYS = 180

# Below this, a notice costs the owner more than the debt is worth. Applied to
# the party's TOTAL actionable amount, not to each bill: five bills of Rs 1,500
# are one Rs 7,500 claim.
NOTICE_FLOOR = 5000

# MSMED s.16 interest = three times the RBI bank rate, compounded monthly.
# The rate moves, so the notice prints the rate it used and tells the reader to
# confirm it. Update this when the RBI moves the bank rate.
RBI_BANK_RATE_PCT = Decimal("5.5")
MSMED_MULTIPLIER = 3

ACT_NOW = "act_now"
NOTICE = "notice"
SMALL = "small"
TIME_BARRED = "time_barred"

# Label + colour per state, so the dashboard and the party page cannot drift.
STATE_UI = {
    ACT_NOW: ("Act before {deadline}", "#b3261e"),
    NOTICE: ("Notice worth sending", "#c0392b"),
    SMALL: ("Too small to chase", "#8a8a84"),
    TIME_BARRED: ("Time barred - write off", "#787774"),
}


def _today() -> _dt.date:
    """Today in IST. The server runs on UTC, where before 5:30 AM it is still
    yesterday - and a one-day error on a limitation deadline is not a rounding
    error, it is a lost claim."""
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).date()


def _as_date(v) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _money(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal(0)


def runway_days(due_date, today: _dt.date | None = None) -> int | None:
    """Days left before this debt becomes unenforceable. None if the due date
    cannot be read - a guessed date must never drive a legal decision."""
    due = _as_date(due_date)
    if due is None:
        return None
    return LIMITATION_DAYS - ((today or _today()) - due).days


def expiry_date(due_date) -> _dt.date | None:
    due = _as_date(due_date)
    return None if due is None else due + _dt.timedelta(days=LIMITATION_DAYS)


def party_legal(bills, today: _dt.date | None = None,
                floor: int = NOTICE_FLOOR) -> dict | None:
    """The one legal state for a party, from its open bills.

    Returns None while the party still has road left in the cadence, so the
    caller can keep showing the normal reminder schedule. Otherwise a dict with
    state, label, colour, the actionable amount, the amount already lost, and
    the date to act before.
    """
    today = today or _today()
    urgent = Decimal(0)          # in time, but expiring within URGENT_DAYS
    live = Decimal(0)            # past the cadence and still enforceable
    dead = Decimal(0)            # past three years - gone
    deadline: _dt.date | None = None
    oldest_days = 0

    for b in bills or []:
        due = _as_date(b.get("due_date"))
        if due is None:
            continue
        amt = _money(b.get("outstanding"))
        if amt <= 0:
            continue
        past_due = (today - due).days
        rw = LIMITATION_DAYS - past_due
        if rw < 0:
            dead += amt
            continue
        # rw == 0 is the LAST day, not the day after: a claim filed on the day
        # the period runs out is still in time. Rounding that the other way
        # would tell an owner to write off money they could still have had.
        if past_due <= OVERDUE_WINDOW_DAYS:
            continue             # still being chased by WhatsApp
        live += amt
        oldest_days = max(oldest_days, past_due)
        if rw <= URGENT_DAYS:
            urgent += amt
            exp = due + _dt.timedelta(days=LIMITATION_DAYS)
            if deadline is None or exp < deadline:
                deadline = exp

    if live > 0:
        if live < floor:
            state = SMALL
        elif urgent > 0:
            state = ACT_NOW
        else:
            state = NOTICE
    elif dead > 0:
        state = TIME_BARRED
    else:
        return None

    label, color = STATE_UI[state]
    if state == SMALL and deadline is not None:
        # Small AND expiring. "Too small to chase" alone would hide a date that
        # is about to pass, so say both: the owner may still want the Rs 500
        # part payment that buys another three years.
        label = "Too small, expires {deadline}"
    return {
        "state": state,
        "label": label.format(deadline=deadline.strftime("%d %b %Y") if deadline else "soon"),
        "color": color,
        "amount": live,
        "lost": dead,
        "deadline": deadline,
        "oldest_days": oldest_days,
    }


def msmed_interest(principal, days: int,
                   bank_rate_pct: Decimal = RBI_BANK_RATE_PCT) -> Decimal:
    """MSMED s.16 interest: three times the RBI bank rate, compounded monthly.

    An estimate for the owner and their advocate to check, not a computation to
    rely on - the rate in force on the appointed day is what the forum applies,
    and that rate changes.
    """
    p = _money(principal)
    if p <= 0 or days <= 0:
        return Decimal(0)
    monthly = (Decimal(bank_rate_pct) * MSMED_MULTIPLIER) / Decimal(1200)
    months = Decimal(days) / Decimal(30)
    # (1 + r)^m with a fractional exponent, via float - this is an estimate.
    grown = p * Decimal(str((1 + float(monthly)) ** float(months)))
    return (grown - p).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _inr(v) -> str:
    """1234567 -> 12,34,567. Indian grouping, because a notice that writes
    rupees the western way reads as foreign."""
    n = int(_money(v).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    s, neg = str(abs(n)), n < 0
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + s


def is_micro_small(udyam_number: str | None) -> bool:
    """Does the shop hold a Udyam registration? This single fact decides the
    whole route: MSME ODR and statutory interest, or an ordinary civil claim."""
    return bool((udyam_number or "").strip())


def procedure(udyam_number: str | None = None) -> list[tuple[str, str]]:
    """The steps, in order, for the owner to follow after the notice goes out.
    (heading, what to do). Reflects the MSMED route when the shop is registered
    under Udyam and the ordinary civil route when it is not."""
    common_head = [
        ("1. Check what is still in time",
         "A claim for the price of goods sold runs out about three years after "
         "the money became due. ASVA marks the parties where that is close. "
         "Anything already past three years cannot be recovered - clear it out "
         "of your books instead of spending money on it."),
        ("2. Try for a part payment first",
         "Any payment, however small, restarts the three years from the date it "
         "is received. On a debt that is close to expiring this is cheaper and "
         "faster than a notice: ask for whatever they will pay today, and get "
         "it into the ledger before the date passes."),
        ("3. Send the final demand on WhatsApp",
         "ASVA sends this for you from your own number: the full ledger, the "
         "total, and a line saying this is the last reminder before you "
         "escalate. Most money that comes back, comes back here."),
    ]
    if is_micro_small(udyam_number):
        return common_head + [
            ("4. Get the demand notice signed and served",
             "ASVA drafts it with your ledger extract and the MSMED interest "
             "claim. Your advocate signs it and sends it by registered post AD "
             "(keep the receipt) and by email. Give them 15 days to pay."),
            ("5. File on the MSME ODR portal",
             "Your Udyam registration lets you file a delayed-payment case at "
             "odr.msme.gov.in. MSME Samadhaan no longer takes new filings. "
             "Under s.15 the buyer had to pay within 45 days (15 if there is no "
             "written agreement), and under s.16 interest runs at three times "
             "the RBI bank rate, compounded monthly - on an old debt that often "
             "comes to more than the bill itself."),
            ("6. Conciliation, then arbitration",
             "The MSEFC conciliates first under s.18. If that fails it goes to "
             "arbitration and makes an award. A buyer who wants to appeal an "
             "award has to deposit 75% of it first, which is where most of the "
             "pressure in this route actually comes from."),
            ("Who to contact",
             "MSME ODR portal: odr.msme.gov.in. Your District Industries Centre "
             "runs the MSEFC for your district. For the notice itself, any "
             "commercial advocate - this is routine work for them."),
        ]
    return common_head + [
        ("4. Register under Udyam first - it is free",
         "Without a Udyam registration you cannot claim the MSMED statutory "
         "interest and you cannot use the MSME ODR portal, which is the "
         "cheapest route there is. Registering at udyamregistration.gov.in "
         "costs nothing and takes a few minutes with your Aadhaar and PAN. Add "
         "the number in ASVA Settings and these steps change to the MSME route. "
         "It does not help for a debt that has already expired, so do it now "
         "rather than when you next need it."),
        ("5. Get the demand notice signed and served",
         "ASVA drafts it with your ledger extract. Your advocate signs it and "
         "sends it by registered post AD (keep the receipt) and by email. Give "
         "them 15 days to pay."),
        ("6. Pre-institution mediation, then a summary suit",
         "For a commercial claim, mediation under s.12A of the Commercial "
         "Courts Act comes before filing, unless you need urgent interim "
         "relief. If that fails, a summary suit under Order XXXVII CPC is the "
         "fast route: the other side needs the court's permission even to "
         "defend it."),
        ("Who to contact",
         "A commercial advocate for the notice and the filing. Udyam "
         "registration: udyamregistration.gov.in."),
    ]


def notice_text(*, shop: str, shop_address: str | None, party: str,
                bills: list, total, udyam_number: str | None = None,
                today: _dt.date | None = None,
                oldest_days: int = 0) -> str:
    """A demand notice DRAFT, for the owner's advocate to check, sign and serve.

    ASVA deliberately does not send this. Service has to be provable, drafting
    and signing for another party is an advocate's job, and an unsigned notice
    produced by software is weak evidence of anything. What ASVA can do is
    everything tedious: the ledger extract, the totals, the interest claim and
    the statutory references, so the advocate's time goes on the parts that
    need judgement.
    """
    today = today or _today()
    msme = is_micro_small(udyam_number)
    lines = []
    for b in bills:
        due = _as_date(b.get("due_date"))
        inv = _as_date(b.get("invoice_date"))
        lines.append(
            f"  {str(b.get('invoice_number') or '-')[:18]:<18} "
            f"{(inv.strftime('%d-%m-%Y') if inv else '-'):<12} "
            f"{(due.strftime('%d-%m-%Y') if due else '-'):<12} "
            f"Rs {_inr(b.get('outstanding')):>12}")
    ledger = "\n".join(lines) or "  (no open bills)"
    interest = msmed_interest(total, oldest_days)
    rate = Decimal(RBI_BANK_RATE_PCT) * MSMED_MULTIPLIER

    head = [
        "DRAFT - FOR YOUR ADVOCATE TO CHECK, SIGN AND SERVE. NOT YET SENT.",
        "",
        "NOTICE OF DEMAND",
        "",
        f"Date: {today.strftime('%d %B %Y')}",
        "",
        "From:",
        f"  {shop}",
    ]
    head += [f"  {l}" for l in (shop_address or "[your shop address]").splitlines()]
    if msme:
        head.append(f"  Udyam Registration No.: {udyam_number.strip()}")
    head += [
        "",
        "To:",
        f"  {party}",
        "  [customer's address]",
        "",
        "Sub: Demand for payment of Rs " + _inr(total) + " towards goods sold and delivered.",
        "",
        "Sir / Madam,",
        "",
        "1. We have supplied goods to you from time to time against the invoices",
        "   listed below. The goods were received by you without any complaint as",
        "   to quantity, quality or price, and the invoices remain unpaid.",
        "",
        "   Invoice            Date         Due date            Amount",
        ledger,
        f"   {'-' * 56}",
        f"   {'TOTAL':<18} {'':12} {'':12} Rs {_inr(total):>12}",
        "",
        "2. Despite repeated reminders, the above amount has not been paid. The",
        f"   oldest of these amounts has been outstanding for {oldest_days} days.",
        "",
    ]
    if msme:
        head += [
            "3. We are a micro / small enterprise registered under the Micro, Small",
            "   and Medium Enterprises Development Act, 2006. Under section 15 of",
            "   that Act you were required to pay within the agreed period, and in",
            "   any case within forty-five days of acceptance of the goods. Under",
            "   section 16, interest is payable at three times the bank rate",
            "   notified by the Reserve Bank of India, compounded monthly, from the",
            "   appointed day.",
            "",
            f"   Interest so computed is approximately Rs {_inr(interest)} "
            f"(at 3 x {RBI_BANK_RATE_PCT}% = {rate}% per annum,",
            "   compounded monthly), to be confirmed at the rate in force.",
            "",
            "4. You are called upon to pay the said sum of Rs " + _inr(total) + " together",
            "   with interest under section 16 within FIFTEEN (15) DAYS of receipt of",
            "   this notice.",
            "",
            "5. Should you fail to do so, we shall refer the matter to the Micro and",
            "   Small Enterprises Facilitation Council under section 18 of the said",
            "   Act, through the Government of India MSME online dispute resolution",
            "   portal, and shall claim the said interest in full, entirely at your",
            "   risk as to costs and consequences.",
        ]
    else:
        head += [
            "3. You are called upon to pay the said sum of Rs " + _inr(total) + " within",
            "   FIFTEEN (15) DAYS of receipt of this notice.",
            "",
            "4. Should you fail to do so, we shall be constrained to initiate",
            "   recovery proceedings, including a summary suit under Order XXXVII of",
            "   the Code of Civil Procedure, 1908, for the said sum together with",
            "   interest and costs, entirely at your risk as to costs and",
            "   consequences.",
        ]
    head += [
        "",
        "This notice is issued without prejudice to our rights and remedies.",
        "",
        "Yours faithfully,",
        "",
        "",
        f"For {shop}",
        "[Proprietor / Authorised signatory]",
        "",
        "---",
        "Before this goes out:",
        "  - have an advocate read and sign it (do not send it unsigned)",
        "  - fill in both addresses and the customer's GSTIN if you have it",
        "  - send it by registered post AD and keep the receipt, and by email",
        "  - attach copies of the invoices and the ledger extract",
    ]
    return "\n".join(head)
