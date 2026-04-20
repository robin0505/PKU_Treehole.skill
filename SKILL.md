---
name: pku-treehole
description: "Use when Codex needs to retrieve, search, browse, filter, summarize, or export content from treehole.pku.edu.cn via a locally logged-in Chrome debug session on port 9222. Uses the logged-in page directly instead of cookie extraction. Supports keyword search, post details with replies, recent posts, tags, and Markdown export."
---

# PKU Treehole

## Overview

Use the bundled Python client and CLI to access Treehole through a locally logged-in Chrome debug session on port `9222`. The current implementation reads content directly from the logged-in page state and page-native methods; it does not extract cookies.

> Location note: The scripts are inside this skill directory (for example, `/Users/robin/.codex/skills/pku-treehole/scripts/`), not in the target repository root. Run commands from this skill folder, or use the full script path.

## Prerequisites

- Use `/Users/robin/miniconda3/bin/python` from the Conda `base` environment.
- Ensure `requests` and `playwright` are installed in that environment.
- Ensure Chrome was started with `--remote-debugging-port=9222` and is already logged into `https://treehole.pku.edu.cn/web/`.
## Workflow

1. **Connection Check**: Directly run `scripts/treehole_cli.py` with `/Users/robin/miniconda3/bin/python` first. If an error occurs, then test `http://localhost:9222/json/version` or ask the user to ensure Chrome is launched correctly and logged in.
2. **Search Strategy**: Generally, start by using `search` to look for keywords. If you find a post with a long comment section that appears informative, use the `post <id> --all-comments` command to fetch the full discussion.
3. **Data Organization**: Always organize relevant raw post data into a dedicated folder (e.g., `raw_data/`), and place generated summaries into a separate folder (e.g., `summaries/`).
4. For standard operations, use the CLI commands:
   - `search`: keyword search, optional tag filter, optional JSON output
   - `latest`: recent posts with optional hot-post filtering
   - `post`: single post plus replies
   - `tags`: available tag list
   - `export-markdown`: export filtered posts to a Markdown file
3. For custom analysis or one-off transforms, import `scripts/treehole_client.py` into a short task script instead of rewriting the Chrome connection, throttling, or page extraction logic.
4. Keep every action strictly serial. Never use concurrent fetching, tab fan-out, or aggressive refresh loops.
5. Open a fresh same-session page for automation so the user's visible Treehole tab is not disturbed.

## Common Commands

```bash
/Users/robin/miniconda3/bin/python scripts/treehole_cli.py search "数学期末" --pages 5
/Users/robin/miniconda3/bin/python scripts/treehole_cli.py post 8164148 --all-comments
/Users/robin/miniconda3/bin/python scripts/treehole_cli.py latest --pages 3 --min-likes 5 --min-replies 10
/Users/robin/miniconda3/bin/python scripts/treehole_cli.py export-markdown treehole_job.md --tag-id 3 --pages 5
```

## References

- Read [references/api-and-data.md](references/api-and-data.md) for setup, page model, tag IDs, data fields, rate limiting, and troubleshooting.
- Read [references/task-templates.md](references/task-templates.md) when you need ready-made Python snippets for search, detail retrieval, hot-post browsing, or Markdown export.

## Guardrails

- Prefer the bundled page client over ad-hoc DOM scraping scripts.
- Respect the built-in throttling in `treehole_client.py`; do not bypass it.
- Keep one task reasonably small; if the user requests a very large crawl, split it into batches with pauses between runs.
- `bookmarks` is not currently supported in direct-page mode.
