---
name: remember
description: User-initiated memory store. Use when the user says "/remember", "remember this", "retiens ça", "note ça" — they have asked for it, so skip the judgement about whether it is worth keeping and go straight to a draft. Delegates the draft format and the storing to the memory-store skill.
---

# /remember — store because the user asked

The user has explicitly asked you to remember something, so the "is this worth
keeping?" judgement is already made. Do **not** re-litigate it; go straight to a
draft.

1. Take the user's gist (`/remember <text>`, or the thing they just pointed at).
2. Follow the **memory-store** skill from "How to propose" onward: show the full
   draft (scope, type, tags from `memory/tags.yaml`, importance, title, body)
   and ask the one question, **"Store this?"**.
3. Store **only on an explicit yes**, exactly as memory-store describes — the
   heredoc `bin/memory add … --body-file -` call, near-duplicate handling, the
   one-line confirmation afterwards.

The only difference from an ordinary proposal is step 1: you are not deciding
whether to bring it up, only how to phrase it. Everything about confirmation and
storage is unchanged — asking is still mandatory.
