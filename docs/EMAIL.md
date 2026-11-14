# Daily email

The pipeline never sends on its own in the default mode (`EMAIL_MODE=claude`). It writes:

- `state/digests/pending.json` → `{"pending": ["2026-11-14"], ...}` (empty list on quiet days)
- `state/digests/<date>.json` → `{subject, recipient, html, text, cars[...]}`

A Claude scheduled task ("GT Scout morning email", daily 7:12 AM America/Chicago) does the
delivery with the Gmail connection:

1. Clones the `state` branch and reads `digests/pending.json`. Empty list: sends nothing.
2. Takes the oldest pending date. Skips if `digests/<date>.sent.json` exists or Gmail's Sent folder
   already holds the subject token `[PGT-<date>]`.
3. Sends one email (subject, HTML and text exactly as stored).
4. Commits `digests/<date>.sent.json` to the `state` branch. The next pipeline run imports the
   receipt and marks the digest `sent`.

Known limit: the Gmail connector removes remote images from HTML, so photos do not appear in the
email; each car links to its full gallery on the site.

To send from the workflow instead, set variable `EMAIL_MODE=smtp` and the `SMTP_*` secrets
(for Gmail: an app password); the run then sends and marks the digest in one step, and photos
are kept in the email. Turn the Claude task off if you switch.

# Seller outreach

`python -m pgt outreach <VIN>` prints a private brief for one car: a target price (the level at
which the car is a "great" deal against comparables), an opening figure, a ceiling, the open
questions that decide the match, and a first-contact email draft. The first email asks; it
carries no numbers. Briefs, seller contact emails and the outreach log (`outreach/<VIN>.json`
on the `state` branch) stay in the private repository and are never exported to the site.
Nothing is sent by the pipeline: every message is approved by the owner before it leaves Gmail.
