# Task Templates

## Contents

- Search and summarize
- Fetch one post with replies
- Browse hot recent posts
- Export Markdown

## Search and summarize

```python
from treehole_client import build_client_from_chrome
import datetime

client = build_client_from_chrome()
results = []

for post in client.search_all("数学期末", max_pages=5):
    results.append(
        {
            "id": f"#{post['pid']}",
            "time": datetime.datetime.fromtimestamp(post["timestamp"]).strftime("%m-%d %H:%M"),
            "likes": post["likenum"],
            "replies": post["reply"],
            "preview": post["text"][:80].replace("\n", " "),
        }
    )

results.sort(key=lambda item: item["likes"], reverse=True)
client.close()
```

## Fetch one post with replies

```python
from treehole_client import build_client_from_chrome
import datetime

pid = 8164148
client = build_client_from_chrome()
post = client.get_post(pid)
comments = client.get_comments(pid, limit=100)

print(f"#{post['pid']} {datetime.datetime.fromtimestamp(post['timestamp'])}")
print(post["text"])
for comment in comments:
    who = "洞主" if comment.get("islz") else comment.get("name", "?")
    print(who, comment["text"])

client.close()
```

For very large threads, replace `get_comments()` with `get_comments_paged()`.

## Browse hot recent posts

```python
from treehole_client import build_client_from_chrome

client = build_client_from_chrome()
hot_posts = []

for post in client.iter_posts(max_pages=3):
    if post["likenum"] >= 5 or post["reply"] >= 10:
        hot_posts.append(post)

client.close()
```

## Export Markdown

```python
from treehole_client import build_client_from_chrome
from pathlib import Path

client = build_client_from_chrome()
lines = ["# 树洞导出\n\n"]

for post in client.search_all("", max_pages=5, tag_id=3):
    lines.append(f"## #{post['pid']}\n")
    lines.append(f"👍 {post['likenum']}  💬 {post['reply']}\n\n")
    lines.append(post["text"] + "\n\n---\n\n")

Path("treehole_export.md").write_text("".join(lines), encoding="utf-8")
client.close()
```

If the task is standard, prefer the bundled CLI instead of writing a new script:

```bash
/Users/robin/miniconda3/bin/python scripts/treehole_cli.py search "数学期末" --pages 5
/Users/robin/miniconda3/bin/python scripts/treehole_cli.py post 8164148 --all-comments
/Users/robin/miniconda3/bin/python scripts/treehole_cli.py export-markdown treehole_job.md --tag-id 3 --title 求职经历
```
