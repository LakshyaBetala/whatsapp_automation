# One-click "Send to ASVA" button in Tally

`ASVA_SendBill.tdl` adds a **Send to ASVA** button to the TallyPrime voucher
screen. One click exports that exact invoice as a PDF into `C:\ASVA\bills\`,
and the ASVA app then sends it to the party on WhatsApp automatically.

It uses TallyPrime's **native PDF export** - no Adobe or PDF printer needed.

## Install (one time, ~1 minute)

1. Keep `ASVA_SendBill.tdl` at `C:\ASVA\ASVA_SendBill.tdl`.
2. In TallyPrime: **F1 (Help) -> TDL & Add-On -> F4 (Manage Local TDLs)**.
   - *Load TDLs on Startup* = **Yes**
   - *List of TDL Configuration Files* = `C:\ASVA\ASVA_SendBill.tdl`
3. Press **Ctrl+A** to accept, then **restart TallyPrime**.

## Use

Make a sales invoice as usual. On the right-side button panel you will see
**Send to ASVA** - **click it with the mouse**. A short confirmation appears.
The bill reaches the party in about a minute (ASVA must be running).

Want zero clicks? Open the TDL and un-comment the two `On: Form Accept` lines -
then every saved sales voucher exports itself automatically.

## About the shortcut key (no conflicts)

The button is meant to be **clicked with the mouse** - a mouse click can never
clash with any Tally or add-on shortcut, on any TallyPrime or ERP 9 version.

As a convenience it also carries the keyboard shortcut **Ctrl + Shift + W**.
This was chosen on purpose: Tally's own shortcuts and virtually all add-ons use
single **Alt+letter** / **Ctrl+letter** keys or the **F-keys**, so the
**Ctrl + Shift + letter** space is left free - it won't collide with a
built-in action or another TDL. (A plain `Alt+W` was avoided precisely because
a single Alt-letter can be taken by Tally or another add-on.)

If you ever want a different key, or none at all, edit or delete the single
`Key : Ctrl + Shift + W` line in the `.tdl` - the mouse click keeps working.

## If the button shows an error

TDL runs inside Tally, so the only way to be 100% certain on your exact
TallyPrime build is to try it once. If Tally shows a red error line when you
click the button, **send me the exact wording of that line** - TDL errors name
the precise line, and the fix is quick. The two things a version might want
differently are the PDF format token (`$$SysName:PDF`) and the export-filename
variable (`SVPrintFileName`); both are easy to adjust.

## Will it clash with my other Tally add-ons?

No. Adding a button with `Add: Button` is **additive** - Tally merges it in next
to whatever buttons your other TDLs already add. It does not remove, replace, or
disable any existing add-on or its buttons. Both keep working.

The only thing that can ever clash between two add-ons is if they use the *same
internal name*. Per Tally's own best practice, every name in this file is
prefixed `ASVAWA...` (button `ASVAWABillButton`, function `ASVAWAExportBillFunc`),
so it can't collide with a default name or another add-on.

If your shop runs a heavy custom TDL and you want **zero** risk of any kind, use
the no-TDL fallback below instead - it touches nothing in Tally.

## Guaranteed fallback (works today, no TDL)

If you ever want to skip the button entirely, TallyPrime's built-in export does
the same job:

1. Open the invoice.
2. Press **Ctrl+E** (Export) -> set **File Format = PDF**, **Folder =
   `C:\ASVA\bills`** -> Export.

ASVA matches by voucher number, so **any** PDF whose name contains the voucher
number is picked up - even Tally's default export name works. So the button and
the manual export are interchangeable; the button just saves the keystrokes.
