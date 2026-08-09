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

# Must match generator column width for leader-dot reflow
COL_W = 58


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


def fetch_basic_stats(s: requests.Session) -> dict[str, Any]:
    q = """
    query($login: String!) {
      user(login: $login) {
        followers { totalCount }
        repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {
          totalCount
          nodes { nameWithOwner stargazerCount }
        }
        repositoriesContributedTo(
          first: 1
          includeUserRepositories: true
          contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]
        ) {
          totalCount
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
        "contributed": user["repositoriesContributedTo"]["totalCount"],
        "stars": stars,
        "commits": commits,
        "followers": user["followers"]["totalCount"],
        "repo_names": [r["nameWithOwner"] for r in repos],
    }


def contributor_loc(s: requests.Session, full_name: str) -> tuple[int, int] | None:
    url = f"https://api.github.com/repos/{full_name}/stats/contributors"
    for _ in range(6):
        r = s.get(url, timeout=60)
        if r.status_code == 202:
            time.sleep(1.5)
            continue
        if r.status_code in (204, 404) or r.status_code != 200:
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
        time.sleep(0.35)
    return max(0, added - deleted), added, deleted


def find(root: etree._Element, element_id: str) -> etree._Element | None:
    el = root.find(f".//*[@id='{element_id}']")
    if el is not None:
        return el
    found = root.xpath(f"//*[@id='{element_id}']")
    return found[0] if found else None


def set_text(root: etree._Element, element_id: str, text: str) -> None:
    el = find(root, element_id)
    if el is None:
        print(f"warn: missing id={element_id}", file=sys.stderr)
        return
    el.text = text


def dots_for(label_len: int, value_len: int, budget: int) -> str:
    n = max(1, budget - label_len - 1 - value_len)
    return ("." * n) + " "


def update_svg(stats: dict[str, Any], loc: tuple[int, int, int]) -> None:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(SVG_PATH, parser)
    root = tree.getroot()

    repos_s = str(stats["repos"])
    contrib_s = str(stats["contributed"])
    stars_s = str(stats["stars"])
    commits_s = fmt(stats["commits"])
    followers_s = str(stats["followers"])
    total, added, deleted = loc
    total_s, add_s, del_s = fmt(total), fmt(added), fmt(deleted)

    mid = " | "
    left_label = "Repos:"
    right_label = "Stars:"
    left_core = f"{repos_s} {{Contributed: {contrib_s}}}"
    left_budget = int((COL_W - len(mid)) * 0.58)
    right_budget = COL_W - len(mid) - left_budget
    set_text(root, "repo_dots", dots_for(len(left_label), len(left_core), left_budget))
    set_text(root, "repo_data", repos_s)
    set_text(root, "contrib_data", contrib_s)
    set_text(root, "star_dots", dots_for(len(right_label), len(stars_s), right_budget))
    set_text(root, "star_data", stars_s)

    half = (COL_W - len(mid)) // 2
    set_text(root, "commit_dots", dots_for(len("Commits:"), len(commits_s), half))
    set_text(root, "commit_data", commits_s)
    right_b = COL_W - len(mid) - half
    set_text(root, "follower_dots", dots_for(len("Followers:"), len(followers_s), right_b))
    set_text(root, "follower_data", followers_s)

    loc_label = "Lines of Code on GitHub:"
    loc_value = f"{total_s} ( {add_s}++ , {del_s}-- )"
    set_text(root, "loc_dots", dots_for(len(loc_label), len(loc_value), COL_W))
    set_text(root, "loc_data", total_s)
    set_text(root, "loc_add", f"{add_s}++")
    set_text(root, "loc_del", f"{del_s}--")

    tree.write(SVG_PATH, xml_declaration=True, encoding="utf-8")
    print(
        f"updated {SVG_PATH}: repos={repos_s} contributed={contrib_s} "
        f"stars={stars_s} commits={commits_s} followers={followers_s} "
        f"loc={total_s} (+{add_s}/-{del_s})"
    )


def main() -> int:
    s = session()
    basic = fetch_basic_stats(s)
    repo_names = basic.pop("repo_names")
    print(
        f"repos={basic['repos']} contributed={basic['contributed']} "
        f"stars={basic['stars']} commits={basic['commits']} followers={basic['followers']}"
    )
    print(f"counting LoC across {len(repo_names)} repos…")
    loc = fetch_loc(s, repo_names)
    update_svg(basic, loc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
