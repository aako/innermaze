# Innermaze independent-chat agent handoffs

This directory is the shared GitHub read hub for Agent 0 (coordination) and Agents A–E. Begin with `AGENT_0_COORDINATION.md`, then read `../INTEGRATION_CONTRACT.md` (proposed), the actual current `main` code, and your own status document. The branch hosting these documents may be separate from `main` until it receives an approved merge.

ChatGPT connected GitHub can fetch repository content but branch/issue/commit writes returned HTTP 403. Therefore agent-written ZIP/patch/Library artifacts are **not on GitHub** until the user pushes using the authenticated Mango Git client. Never assume another agent saw a private conversation or a downloadable artifact. Do not commit transcripts, personal recording filenames, audio, private logs or secrets to the public repo.

Each agent publishes exact checkout/checkin metadata in its own status Markdown file on its own branch. Agent E is the integration gate. Agent 0 approves shared decisions. The user publishes and authorizes real-device tests and releases.
