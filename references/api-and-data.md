# API And Data Reference

## Contents

- Environment
- Access model
- Safety rules
- Page model
- Data fields
- Troubleshooting

## Environment

Use the Conda `base` Python at `/Users/robin/miniconda3/bin/python` and ensure these packages are installed there:

```bash
/Users/robin/miniconda3/bin/python -m pip install requests playwright
/Users/robin/miniconda3/bin/playwright install chromium
```

Start Chrome with remote debugging enabled, then log into Treehole in that browser profile.

macOS:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug
```

Linux:

```bash
google-chrome \
  --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug
```

## Access model

Use Chrome DevTools Protocol to attach to the already logged-in local Chrome instance. Then open a fresh page in the same browser context and read Treehole content through the page's own Vue state and page-native methods.

Preferred order:

1. Attach to Chrome on port `9222`.
2. Open a fresh same-session Treehole page.
3. Reuse the page's own `headerTop.search()` and `index.list` data.
4. Read replies from the in-page `reply` components.

## Safety rules

- Keep actions strictly serial. Do not use `ThreadPoolExecutor`, `asyncio.gather`, or similar fan-out patterns.
- Do not aggressively refresh the same keyword or PID.
- Do not open multiple automation tabs at once.
- Keep one run modest. As a rule of thumb, avoid going past `max_pages=20` in a single task.

Rate-limit assumptions bundled into the client:

| Item | Value |
| --- | --- |
| Rate target | 18 page actions / 60 seconds |
| Minimum gap | ~3.3s + random jitter |
| Page settle wait | 2.5s |
| Scroll settle wait | 2.5s |

## Page Model

The current skill reads from the logged-in web app rather than directly calling public REST endpoints.

Useful component/data entry points:

- `index.list`: current post list already rendered in page state
- `headerTop.search()`: trigger keyword search
- search keyword `#PID`: fetch one post by PID
- `reply.data`: replies already loaded for one post

Behavior notes:

- A fresh page starts on the latest feed.
- Search results are loaded through the page itself.
- Additional results are loaded conservatively by scrolling.
- Tag IDs are mapped locally using the known built-in labels.

Known tag IDs:

| ID | Name |
| --- | --- |
| `1` | 课程心得 |
| `2` | 失物招领 |
| `3` | 求职经历 |
| `5` | 跳蚤市场 |

## Data fields

### Post

| Field | Type | Meaning |
| --- | --- | --- |
| `pid` | int | post ID |
| `text` | str | post body |
| `type` | str | usually empty or `"image"` |
| `timestamp` | int | Unix timestamp in seconds |
| `likenum` | int | likes |
| `reply` | int | reply count |
| `tag` | str/null | tag name |
| `url` | str/null | image URL when present |

### Comment

| Field | Type | Meaning |
| --- | --- | --- |
| `cid` | int | comment ID |
| `pid` | int | post ID |
| `text` | str | reply body |
| `timestamp` | int | Unix timestamp |
| `name` | str | anonymous display name |
| `islz` | int | `1` when the author is the original poster |
| `quote_text` | str/null | quoted snippet |

## Troubleshooting

### Cannot connect to the debug port

Chrome is not running with `--remote-debugging-port=9222`, or it is using a different port.

### The page opens but the skill says Treehole is not logged in

Reopen `https://treehole.pku.edu.cn/web/` in the debug Chrome instance and confirm the feed is visible before running the skill.

### Search results look unrelated

A blank keyword means the latest feed rather than search mode. Pass a non-empty keyword when you want search semantics.

### `get_comments()` returns only part of a hot thread

Very hot threads may render replies incrementally in the page. Retry with `--all-comments`, keep the batch small, and avoid repeated refreshes.

### `bookmarks` fails

Bookmarks are not currently exposed in the direct-page reader.
