# Design: ASVA

A locked design system for tryasva.com AND the ASVA desktop app (the
server-rendered screens in app/routers/admin.py shown inside desktop/). Every
page redesign reads this file before emitting code. Extend or amend it when
the system needs to grow; never re-pick per page.

Audience: Indian wholesale shop owners, 25 to 70, on TallyPrime, often on a
phone, reading English, Hinglish or Hindi. Not tech people.
Job: get stuck credit paid through polite WhatsApp reminders.
Tone: plain and trustworthy, like a well-kept ledger. No startup gloss.

Copy: English first, everywhere. Hinglish or Hindi appears only where the viewer
picked that language (the app's language switch, a batch set to Hinglish). No
Hindi words inside English copy: "credit" not "udhaar", "balance" not "baki".
No em or en dashes in any copy. No emoji.

## Genre
modern-minimal, custom theme "Ledger" (vibe: "account-book calm, plain,
trustworthy, Indian"). Axes: light / grotesk-sans / cool.

## Macrostructure family
- Marketing pages (home, how it works, features, use cases, compare,
  download): Workbench. The real product is the proof: the actual reminder
  text a customer receives, and real screenshots of the app. Two-column hero,
  title left, the real artifact right. No fake phone or browser frames.
- Pricing: Conversational FAQ under a plain plan table.
- App screens (Today, Payments, Parties, Reminders, Settings): working
  screens. No enrichment, no marketing type. Numbers first, one primary
  action per row.
- Content pages (guide, privacy, terms): Long Document.

## Theme
Values are OKLCH; the hex beside each is what ships (old Android WebViews and
older Electron builds do not all parse oklch()).
- `--color-paper`    oklch(98.5% 0.006 255) #f7fafe   page
- `--color-paper-2`  oklch(96.2% 0.008 255) #eff3f8   raised surface, table header
- `--color-paper-3`  oklch(93.5% 0.010 255) #e5eaf0   pressed / selected row
- `--color-ink`      oklch(22% 0.030 258) #121b28     text, navy ink
- `--color-ink-2`    oklch(38% 0.026 258) #3a4350     secondary text
- `--color-muted`    oklch(52% 0.020 258) #626a75     hints, captions
- `--color-rule`     oklch(86% 0.014 255) #cbd2da     dividers, borders
- `--color-rule-2`   oklch(91.5% 0.010 255) #dfe3ea   faint row lines
- `--color-accent`   oklch(47% 0.160 262) #2454b3     ledger blue: primary buttons, links, active tab
- `--color-accent-2` oklch(40% 0.160 262) #0f3f9c     accent hover / pressed
- `--color-accent-ink` oklch(98.5% 0.006 255) #f7fafe text on accent
- `--color-focus`    oklch(52% 0.200 262) #1f5ed9     focus ring only
- Meaning colours (never decoration):
  - paid     `--color-paid` #10703a on `--color-paid-bg` #e0f5e5
  - due      `--color-due` #8a5600 on `--color-due-bg` #fdefd2
  - overdue  `--color-late` #a82e29 on `--color-late-bg` #ffeae7
- Dark (desktop app only; the website is light only, like paper): paper
  #0a1018 / #121922 / #1d2430, ink #e8ebf1 / #bfc5cc, muted #9299a2, rule
  #2c333e / #1e242e, accent #77a4f6 (hover #91b7fe), paid #72cf8e on #193521,
  due #edbb64 on #3f2c10, overdue #f98f84 on #47211d.

## Typography
- One family, Anek (Ek Type, an Indian foundry), so English, हिंदी, தமிழ்
  and ગુજરાતી render as one product: "Anek Latin", "Anek Devanagari",
  "Anek Tamil", "Anek Gujarati" (Google Fonts, variable wght + wdth).
- Display: Anek Latin 680, width 92, tracking -0.02em, roman only.
- Body: Anek Latin 420, width 100, line-height 1.55, measure 64ch.
- Numbers: `font-variant-numeric: tabular-nums` on every amount and date.
- Display scale: `--text-display` clamp(2.4rem, 4.4vw + 0.6rem, 4rem).
- No italic headings, no monospace labels, no uppercase eyebrows (one numbered
  step list on the home page is the only ordinal device).

## Spacing
4-point named scale in tokens.css (`--space-3xs` … `--space-3xl`). Pages use
named tokens, never raw values.

## Motion
- Easing `--ease-out` cubic-bezier(0.16, 1, 0.3, 1); durations 120-200 ms.
- No reveals, no scroll animation, no count-ups, no marquees. Content is
  visible on first paint.
- Hover and press only: colour shift on buttons/links, 1 px press.
- Reduced motion: transitions off.

## Microinteractions stance
- Silent success (the saved value is visible); toasts only for failures.
- Optimistic actions with Undo where reversible; a confirm only when
  irreversible.
- Focus rings instant, 2 px `--color-focus`, offset 2 px.

## CTA voice
- Primary: filled `--color-accent`, `--color-accent-ink` text, radius 10 px,
  44 px min height, label says exactly what happens ("Start 14-day free
  trial", "Remind now", "Post to Tally").
- Secondary: underlined text link in `--color-accent`, or outlined button
  (1 px `--color-rule`) in dense app rows.
- One primary per view section.

## Per-page allowances
- Marketing pages may show real screenshots and the real reminder text.
  Nothing invented: every number on a page is real or absent.
- App pages: no illustration, no decoration. Meaning colours only for status.
- Content pages: typography only.

## What pages MUST share
- The ASVA wordmark (Anek 760, ink) with the blue ledger rule mark.
- Accent colour and its placement (CTAs, links, active tab; <= 5% of a view).
- The Anek family, the type scale, the CTA voice, the meaning colours.

## What pages MAY differ on
- Section order and composition within their family.
- Whether a marketing page shows the message or a screenshot first.

## Exports

### Where the tokens live
- Website: `site.css` at the root of the asva_website repo (every page links it).
- Desktop screens: `_LEDGER_CSS` in app/routers/admin.py, appended to every
  admin page so it wins over the older per-page styles.
- Desktop shell: the `:root` block in desktop/renderer/index.html.
Token names are identical in all three.
