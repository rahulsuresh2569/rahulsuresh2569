#!/usr/bin/env python3
"""Update github-profile-card.svg with live GitHub stats (Andrew6rant-style)."""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import requests
from lxml import etree

USER = os.environ.get("GITHUB_USERNAME", "rahulsuresh2569")
TOKEN = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
SVG_PATH = os.environ.get("SVG_PATH", "github-profile-card.svg")

# Fixed display budgets for leader-dot reflow (must match SVG column width logic)
REPO_BUDGET = 6
STAR_BUDGET = 6
COMMIT_BUDGET = 8
FOLLOWER_BUDGET = 6
LOC_TOTAL_BUDGET = 9
LOC_ADD_BUDGET = 9
LOC_DEL_BUDGET = 8

NS = {"svg": "http://www.w3.org/2000/svg"}


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"{USER}-profile-readme",
        }
    )
    if TOKEN:
        s.headers["Authorization"] = f"Bearer {TOKEN}"
    return s


def fmt(n: int) -> str:
    return f"{n:,}"


def graphql(s: requests.Session, query: str, variables: dict[str, Any] | None = None) -> dict:
    r = s.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables or {}},
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def fetch_basic_stats(s: requests.Session) -> dict[str, int]:
    q = """
    query($login: String!) {
      user(login: $login) {
        followers { totalCount }
        repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {
          totalCount
          nodes { nameWithOwner stargazerCount }
        }
        contributionsCollection {
          totalCommitContributions
          restrictedContributionsCount
        }
      }
    }
    """
    user = graphql(s, q, {"login": USER})["user"]
    repos = user["repositories"]["nodes"]
    stars = sum(r["stargazerCount"] for r in repos)
    commits = (
        user["contributionsCollection"]["totalCommitContributions"]
        + user["contributionsCollection"]["restrictedContributionsCount"]
    )

    # Prefer all-time-ish commit search when token allows; fall back to last-year total
    try:
        sr = s.get(
            "https://api.github.com/search/commits",
            params={"q": f"author:{USER}", "per_page": 1},
            headers={"Accept": "application/vnd.github.cloak-preview+json"},
            timeout=60,
        )
        if sr.status_code == 200:
            total = sr.json().get("total_count")
            if isinstance(total, int) and total > 0:
                commits = total
    except requests.RequestException:
        pass

    return {
        "repos": user["repositories"]["totalCount"],
        "stars": stars,
        "commits": commits,
        "followers": user["followers"]["totalCount"],
        "repo_names": [r["nameWithOwner"] for r in repos],
    }


def contributor_loc(s: requests.Session, full_name: str) -> tuple[int, int] | None:
    """Return (additions, deletions) for USER in one repo via stats API."""
    url = f"https://api.github.com/repos/{full_name}/stats/contributors"
    for _ in range(6):
        r = s.get(url, timeout=60)
        if r.status_code == 202:
            time.sleep(1.5)
            continue
        if r.status_code == 204 or r.status_code == 404:
            return None
        if r.status_code != 200:
            return None
        for person in r.json():
            author = (person.get("author") or {}).get("login")
            if author and author.lower() == USER.lower():
                weeks = person.get("weeks") or []
                added = sum(int(w.get("a", 0)) for w in weeks)
                deleted = sum(int(w.get("d", 0)) for w in weeks)
                return added, deleted
        return None
    return None


def fetch_loc(s: requests.Session, repo_names: list[str]) -> tuple[int, int, int]:
    added = deleted = 0
    for name in repo_names:
        pair = contributor_loc(s, name)
        if not pair:
            continue
        a, d = pair
        added += a
        deleted += d
        time.sleep(0.35)  # be gentle with stats API
    total = max(0, added - deleted)
    return total, added, deleted


def set_text(root: etree._Element, element_id: str, text: str) -> None:
    el = root.find(f".//*[@id='{element_id}']")
    if el is None:
        # try with svg namespace
        el = root.xpath(f"//*[@id='{element_id}']")
        el = el[0] if el else None
    if el is None:
        print(f"warn: missing id={element_id}", file=sys.stderr)
        return
    el.text = text


def justify(root: etree._Element, data_id: str, dots_id: str, value: str, budget: int) -> None:
    """Rewrite value + leader dots so the right edge stays aligned."""
    value = str(value)
    set_text(root, data_id, value)
    room = max(1, budget - len(value))
    if room <= 2:
        dots = {0: "", 1: " ", 2: ". "}[room]
    else:
        dots = " " + ("." * room) + " "
    # Our SVG already has a trailing space after dots tspan content in some places;
    # store dots only (no leading space) to match generated markup: dots + ' ' before value.
    # Generated form: <tspan id="*_dots">.... </tspan>  (dots + one space baked in template)
    # Keep dots without surrounding spaces; template had: dots then literal space in sibling.
    set_text(root, dots_id, ("." * max(1, budget - len(value))) + " ")


def update_svg(stats: dict[str, int], loc: tuple[int, int, int]) -> None:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(SVG_PATH, parser)
    root = tree.getroot()

    justify(root, "repo_data", "repo_dots", str(stats["repos"]), REPO_BUDGET)
    justify(root, "star_data", "star_dots", str(stats["stars"]), STAR_BUDGET)
    justify(root, "commit_data", "commit_dots", fmt(stats["commits"]), COMMIT_BUDGET)
    justify(root, "follower_data", "follower_dots", str(stats["followers"]), FOLLOWER_BUDGET)

    total, added, deleted = loc
    # loc_data is the total number only; ++/-- are separate tspans (suffix in SVG)
    set_text(root, "loc_data", fmt(total) if isinstance(total, int) else str(total))
    set_text(root, "loc_add", f"{fmt(added)}++" if isinstance(added, int) else f"{added}++")
    set_text(root, "loc_del", f"{fmt(deleted)}--" if isinstance(deleted, int) else f"{deleted}--")
    # reflow loc leader dots against a conservative total width
    loc_value_len = len(f"{fmt(total)} ( {fmt(added)}++ , {fmt(deleted)}-- )")
    loc_label_len = len("Lines of Code:")
    # column width used when generating SVG
    col_w = 52
    dots_n = max(1, col_w - loc_label_len - 1 - loc_value_len)
    set_text(root, "loc_dots", ("." * dots_n) + " ")

    tree.write(SVG_PATH, xml_declaration=True, encoding="utf-8")
    print(
        f"updated {SVG_PATH}: repos={stats['repos']} stars={stats['stars']} "
        f"commits={stats['commits']} followers={stats['followers']} "
        f"loc={total} (+{added}/-{deleted})"
    )


def main() -> int:
    s = session()
    basic = fetch_basic_stats(s)
    repo_names = basic.pop("repo_names")
    print(f"repos={basic['repos']} stars={basic['stars']} commits={basic['commits']} followers={basic['followers']}")
    print(f"counting LoC across {len(repo_names)} repos…")
    loc = fetch_loc(s, repo_names)
    update_svg(basic, loc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
