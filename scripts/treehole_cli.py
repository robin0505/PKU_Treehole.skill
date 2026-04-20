#!/Users/robin/miniconda3/bin/python
"""CLI for common PKU Treehole read-only workflows."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _format_timestamp(timestamp: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if timestamp in (None, ""):
        return ""
    return dt.datetime.fromtimestamp(int(timestamp)).strftime(fmt)


def _post_preview(post: Dict[str, Any], width: int = 100) -> str:
    return str(post.get("text", "")).replace("\n", " ")[:width]


def _human_posts(posts: Iterable[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for post in posts:
        lines.append(
            "#{pid}  {time}  👍{likes}  💬{replies}{tag}".format(
                pid=post.get("pid", "?"),
                time=_format_timestamp(post.get("timestamp")),
                likes=post.get("likenum", 0),
                replies=post.get("reply", 0),
                tag="  [{0}]".format(post["tag"]) if post.get("tag") else "",
            )
        )
        lines.append("  {0}".format(_post_preview(post)))
        lines.append("")
    return "\n".join(lines).rstrip()


def _human_post_detail(post: Dict[str, Any], comments: List[Dict[str, Any]]) -> str:
    lines = [
        "=== #{0} ===".format(post.get("pid", "?")),
        "时间：{0}".format(_format_timestamp(post.get("timestamp"), "%Y-%m-%d %H:%M:%S")),
        "👍{0}  💬{1}".format(post.get("likenum", 0), post.get("reply", 0)),
        "",
        str(post.get("text", "")),
        "",
        "--- 回复 ---",
        "",
    ]
    for comment in comments:
        name = "【洞主】" if comment.get("islz") else comment.get("name", "?")
        header = "[{cid}] {name} {time}".format(
            cid=comment.get("cid", "?"),
            name=name,
            time=_format_timestamp(comment.get("timestamp"), "%H:%M"),
        )
        lines.append(header)
        if comment.get("quote_text"):
            lines.append("  > {0}".format(str(comment["quote_text"])[:80]))
        lines.append("  {0}".format(comment.get("text", "")))
        lines.append("")
    return "\n".join(lines).rstrip()


def _dump(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(data)


def _build_client():
    from treehole_client import build_client_from_chrome

    return build_client_from_chrome()


def _materialize(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from treehole_client import materialize

    return materialize(items)


def cmd_search(args: argparse.Namespace) -> None:
    client = _build_client()
    try:
        posts = _materialize(
            client.search_all(
                keyword=args.keyword,
                max_pages=args.pages,
                limit=args.limit,
                tag_id=args.tag_id,
            )
        )
        if args.sort == "likes":
            posts.sort(key=lambda item: (item.get("likenum", 0), item.get("timestamp", 0)), reverse=True)
        else:
            posts.sort(key=lambda item: item.get("timestamp", 0), reverse=True)

        if args.json:
            _dump(posts, as_json=True)
        else:
            _dump(_human_posts(posts), as_json=False)
    finally:
        client.close()


def cmd_latest(args: argparse.Namespace) -> None:
    client = _build_client()
    try:
        posts = _materialize(client.iter_posts(max_pages=args.pages, limit=args.limit))
        if args.min_likes or args.min_replies:
            posts = [
                post
                for post in posts
                if post.get("likenum", 0) >= args.min_likes or post.get("reply", 0) >= args.min_replies
            ]

        if args.json:
            _dump(posts, as_json=True)
        else:
            _dump(_human_posts(posts), as_json=False)
    finally:
        client.close()


def cmd_post(args: argparse.Namespace) -> None:
    client = _build_client()
    if args.debug_timing and hasattr(client, "set_debug_timing"):
        client.set_debug_timing(True)
    try:
        if args.all_comments:
            # Use get_all_comments which fetches via in-page API calls.
            # This loads the post bundle once and gets all comments.
            comments = client.get_all_comments(args.pid)
            # get_post will hit the cache from get_all_comments's bundle
            post = dict(client._load_post_bundle(args.pid, use_api_comments=True).get("post", {}))
        else:
            post = client.get_post(args.pid)
            comments = client.get_comments(args.pid, limit=args.comments_limit)

        if args.json:
            _dump({"post": post, "comments": comments}, as_json=True)
        else:
            _dump(_human_post_detail(post, comments), as_json=False)
    finally:
        client.close()


def cmd_tags(args: argparse.Namespace) -> None:
    client = _build_client()
    try:
        tags = client.get_tags()
        if args.json:
            _dump(tags, as_json=True)
            return

        lines = ["{id}\t{name}".format(id=item.get("id", "?"), name=item.get("name", "")) for item in tags]
        _dump("\n".join(lines), as_json=False)
    finally:
        client.close()


def cmd_bookmarks(args: argparse.Namespace) -> None:
    raise SystemExit("Bookmarks are not available in direct-page mode yet.")


def cmd_export_markdown(args: argparse.Namespace) -> None:
    client = _build_client()
    try:
        posts = _materialize(
            client.search_all(
                keyword=args.keyword,
                max_pages=args.pages,
                limit=args.limit,
                tag_id=args.tag_id,
            )
        )

        title = args.title or "树洞导出"
        lines = ["# {0}\n\n".format(title)]
        for post in posts:
            lines.append(
                "## #{pid} ({time})\n".format(
                    pid=post.get("pid", "?"),
                    time=_format_timestamp(post.get("timestamp")),
                )
            )
            lines.append("👍 {0}  💬 {1}\n\n".format(post.get("likenum", 0), post.get("reply", 0)))
            if post.get("tag"):
                lines.append("标签：{0}\n\n".format(post["tag"]))
            lines.append("{0}\n\n---\n\n".format(post.get("text", "")))

        out_path = Path(args.output).expanduser()
        out_path.write_text("".join(lines), encoding="utf-8")
        print("Wrote {0} ({1} posts)".format(out_path, len(posts)))
    finally:
        client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read PKU Treehole data from a logged-in Chrome debug session.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Search posts by keyword.")
    search.add_argument("keyword", help="Keyword to search. Supports #PID.")
    search.add_argument("--pages", type=int, default=5, help="Maximum number of pages to scan.")
    search.add_argument("--limit", type=int, default=25, help="Items per page.")
    search.add_argument("--tag-id", type=int, help="Optional tag filter.")
    search.add_argument("--sort", choices=("time", "likes"), default="time", help="Output sort order.")
    search.add_argument("--json", action="store_true", help="Emit JSON.")
    search.set_defaults(func=cmd_search)

    latest = subparsers.add_parser("latest", help="Browse recent posts.")
    latest.add_argument("--pages", type=int, default=3, help="Maximum number of pages to scan.")
    latest.add_argument("--limit", type=int, default=25, help="Items per page.")
    latest.add_argument("--min-likes", type=int, default=0, help="Keep posts with at least this many likes.")
    latest.add_argument("--min-replies", type=int, default=0, help="Keep posts with at least this many replies.")
    latest.add_argument("--json", action="store_true", help="Emit JSON.")
    latest.set_defaults(func=cmd_latest)

    post = subparsers.add_parser("post", help="Fetch one post and its replies.")
    post.add_argument("pid", help="Post ID, with or without leading #.")
    post.add_argument("--comments-limit", type=int, default=100, help="Single-request reply limit.")
    post.add_argument("--all-comments", action="store_true", help="Page through all replies until exhausted.")
    post.add_argument(
        "--comments-per-page",
        type=int,
        default=50,
        help="Reply page size when --all-comments is enabled.",
    )
    post.add_argument("--debug-timing", action="store_true", help="Print per-stage timing diagnostics to stderr.")
    post.add_argument("--json", action="store_true", help="Emit JSON.")
    post.set_defaults(func=cmd_post)

    tags = subparsers.add_parser("tags", help="List available tags.")
    tags.add_argument("--json", action="store_true", help="Emit JSON.")
    tags.set_defaults(func=cmd_tags)

    bookmarks = subparsers.add_parser("bookmarks", help="Bookmarks are not yet supported in direct-page mode.")
    bookmarks.add_argument("--json", action="store_true", help="Emit JSON.")
    bookmarks.set_defaults(func=cmd_bookmarks)

    export_md = subparsers.add_parser("export-markdown", help="Export filtered posts to Markdown.")
    export_md.add_argument("output", help="Output Markdown file path.")
    export_md.add_argument("--keyword", default="", help="Search keyword. Empty means latest posts for the tag/home feed.")
    export_md.add_argument("--pages", type=int, default=5, help="Maximum number of pages to scan.")
    export_md.add_argument("--limit", type=int, default=25, help="Items per page.")
    export_md.add_argument("--tag-id", type=int, help="Optional tag filter.")
    export_md.add_argument("--title", help="Optional document title.")
    export_md.set_defaults(func=cmd_export_markdown)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
