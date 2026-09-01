---
id: inbox-marshal
name: Inbox Marshal
repo: https://github.com/PlainJane20/inbox-marshal
invoke: "python run_agent.py --scan --verbose"
tags: [email, gmail, cleanup, spam, receipts, subscriptions]
risk_tier: medium
---

Gmail spam cleanup and receipt/subscription organization. Classifies every
email, files spam into real Gmail labels, and never permanently deletes
anything -- archive + label is the strongest action it takes. `--scan` is
the safe default; `--apply` files things for real with interactive
per-sender confirmation before any unsubscribe.
