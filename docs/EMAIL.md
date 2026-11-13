# Daily email

The pipeline never sends on its own in the default mode (`EMAIL_MODE=claude`). It writes:

- `state/digests/pending.json` → `{"pending": ["2026-11-14"], ...}` (empty list on quiet days)
- `state/digests/<date>.json` → `{subject, recipient, html, text, cars[...]}`

A Claude scheduled task, running each morning in America/Chicago with the GitHub and Gmail
connections, does the delivery. Task prompt (replace OWNER/REPO):

```
You deliver the GT Scout daily email. Repository: OWNER/REPO, branch `state`.

1. Read `digests/pending.json` from the `state` branch (gh api repos/OWNER/REPO/contents/digests/pending.json?ref=state,
   base64-decode the content). If "pending" is empty, stop: send nothing and reply "No new qualifying cars today."
2. Take the OLDEST pending date only. Read `digests/<date>.json` the same way.
3. Idempotency: if `digests/<date>.sent.json` already exists on the branch, or a Gmail search for the exact
   subject token "[PGT-<date>]" in Sent finds a message, do not send; go to step 5 if the receipt is missing.
4. Send ONE email with Gmail to the digest's `recipient`: subject = the digest's `subject`, HTML body = the
   digest's `html` exactly as stored. Do not rewrite, summarise or add content.
5. Write `digests/<date>.sent.json` to the `state` branch with
   {"channel":"gmail","sent_at":"<UTC ISO time>","message_id":"<Gmail id>"} (gh api -X PUT .../contents/...).
6. Never send more than one email per run. If anything fails, send nothing further and report the error.
```

The next pipeline run imports the receipt and marks the digest `sent`. If the receipt write
fails after a successful send, step 3's Gmail check prevents a second email the next morning.

To send from the workflow instead, set variable `EMAIL_MODE=smtp` and the `SMTP_*` secrets
(for Gmail: an app password); the run then sends and marks the digest in one step.
