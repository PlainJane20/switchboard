---
id: spec-review-agent
name: Spec Review Agent
repo: https://github.com/PlainJane20/spec-review-agent
invoke: "python run_review.py {ticket_path}"
tags: [spec-review, ambiguity, feasibility, privacy, completeness, rfc, ownership]
risk_tier: low
---

Parallel AI review of a specification or RFC across five independent
lenses -- ambiguity, feasibility, privacy, completeness, and ownership --
each a separate critic, not one model wearing five hats. A ticket whose
body is a spec/RFC draft goes straight in as-is.
