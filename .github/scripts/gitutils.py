#!/usr/bin/env python3

from collections import defaultdict
from datetime import datetime
from typing import cast, Any, Dict, Iterator, List, Optional, Tuple, Union
import os
import re


RE_GITHUB_URL_MATCH = re.compile("^https://.*@?github.com/(.+)/(.+)$")


def get_git_remote_name() -> str:
    return os.getenv("GIT_REMOTE_NAME", "origin")


def get_git_repo_dir() -> str:
    from pathlib import Path
    return os.getenv("GIT_REPO_DIR", str(Path(__file__).resolve().parent.parent.parent))


def fuzzy_list_to_dict(items: List[Tuple[str, str]]) -> Dict[str, List[str]]:
    """
    Converts list to dict preserving elements with duplicate keys
    """
    rc: Dict[str, List[str]] = defaultdict(lambda: [])
    for (key, val) in items:
        rc[key].append(val)
    return dict(rc)


def _check_output(items: List[str], encoding: str = "utf-8") -> str:
    from subprocess import check_output
    return check_output(items).decode(encoding)


class GitCommit:
    commit_hash: str
    title: str
    body: str
    author: str
    author_date: datetime
    commit_date: Optional[datetime]

    def __init__(self,
                 commit_hash: str,
                 author: str,
                 author_date: datetime,
                 title: str,
                 body: str,
                 commit_date: Optional[datetime] = None) -> None:
        self.commit_hash = commit_hash
        self.author = author
        self.author_date = author_date
        self.commit_date = commit_date
        self.title = title
        self.body = body

    def __repr__(self) -> str:
        return f"{self.title} ({self.commit_hash})"

    def __contains__(self, item: Any) -> bool:
        return item in self.body or item in self.title


def parse_fuller_format(lines: Union[str, List[str]]) -> GitCommit:
    """
    Expect commit message generated using `--format=fuller --date=unix` format, i.e.:
        commit <sha1>
        Author:     <author>
        AuthorDate: <author date>
        Commit:     <committer>
        CommitDate: <committer date>

        <title line>

        <full commit message>

    """
    if isinstance(lines, str):
        lines = lines.split("\n")
    # TODO: Handle merge commits correctly
    if len(lines) > 1 and lines[1].startswith("Merge:"):
        del lines[1]
    assert len(lines) > 7
    assert lines[0].startswith("commit")
    assert lines[1].startswith("Author: ")
    assert lines[2].startswith("AuthorDate: ")
    assert lines[3].startswith("Commit: ")
    assert lines[4].startswith("CommitDate: ")
    assert len(lines[5]) == 0
    return GitCommit(commit_hash=lines[0].split()[1].strip(),
                     author=lines[1].split(":", 1)[1].strip(),
                     author_date=datetime.fromtimestamp(int(lines[2].split(":", 1)[1].strip())),
                     commit_date=datetime.fromtimestamp(int(lines[4].split(":", 1)[1].strip())),
                     title=lines[6].strip(),
                     body="\n".join(lines[7:]),
                     )


class GitRepo:
    def __init__(self, path: str, remote: str = "origin") -> None:
        self.repo_dir = path
        self.remote = remote

    def _run_git(self, *args: Any) -> str:
        return _check_output(["git", "-C", self.repo_dir] + list(args))

    def revlist(self, revision_range: str) -> List[str]:
        rc = self._run_git("rev-list", revision_range, "--", ".").strip()
        return rc.split("\n") if len(rc) > 0 else []

    def current_branch(self) -> str:
        return self._run_git("symbolic-ref", "--short", "HEAD").strip()

    def checkout(self, branch: str) -> None:
        self._run_git('checkout', branch)

    def show_ref(self, name: str) -> str:
        refs = self._run_git('show-ref', '-s', name).strip().split('\n')
        if not all(refs[i] == refs[0] for i in range(1, len(refs))):
            raise RuntimeError(f"referce {name} is ambigous")
        return refs[0]

    def rev_parse(self, name: str) -> str:
        return self._run_git('rev-parse', '--verify', name).strip()

    def get_merge_base(self, from_ref: str, to_ref: str) -> str:
        return self._run_git('merge-base', from_ref, to_ref).strip()

    def patch_id(self, ref: Union[str, List[str]]) -> List[Tuple[str, str]]:
        is_list = isinstance(ref, list)
        if is_list:
            if len(ref) == 0:
                return []
            ref = " ".join(ref)
        rc = _check_output(['sh', '-c', f'git -C {self.repo_dir} show {ref}|git patch-id --stable']).strip()
        return [cast(Tuple[str, str], x.split(" ", 1)) for x in rc.split("\n")]

    def get_commit(self, ref: str) -> GitCommit:
        return parse_fuller_format(self._run_git('show', '--format=fuller', '--date=unix', '--shortstat', ref))

    def cherry_pick(self, ref: str) -> None:
        self._run_git('cherry-pick', '-x', ref)

    def compute_branch_diffs(self, from_branch: str, to_branch: str) -> Tuple[List[str], List[str]]:
        """
        Returns list of commmits that are missing in each other branch since their merge base
        Might be slow if merge base is between two branches is pretty far off
        """
        from_ref = self.rev_parse(from_branch)
        to_ref = self.rev_parse(to_branch)
        merge_base = self.get_merge_base(from_ref, to_ref)
        from_commits = self.revlist(f'{merge_base}..{from_ref}')
        to_commits = self.revlist(f'{merge_base}..{to_ref}')
        from_ids = fuzzy_list_to_dict(self.patch_id(from_commits))
        to_ids = fuzzy_list_to_dict(self.patch_id(to_commits))
        for patch_id in set(from_ids).intersection(set(to_ids)):
            from_values = from_ids[patch_id]
            to_values = to_ids[patch_id]
            if len(from_values) != len(to_values):
                # Eliminate duplicate commits+reverts from the list
                while len(from_values) > 0 and len(to_values) > 0:
                    frc = self.get_commit(from_values.pop())
                    toc = self.get_commit(to_values.pop())
                    if frc.title != toc.title or frc.author_date != toc.author_date:
                        raise RuntimeError(f"Unexpected differences between {frc} and {toc}")
                    from_commits.remove(frc.commit_hash)
                    to_commits.remove(toc.commit_hash)
                continue
            for commit in from_values:
                from_commits.remove(commit)
            for commit in to_values:
                to_commits.remove(commit)
        return (from_commits, to_commits)

    def cherry_pick_commits(self, from_branch: str, to_branch: str) -> None:
        orig_branch = self.current_branch()
        self.checkout(to_branch)
        from_commits, to_commits = self.compute_branch_diffs(from_branch, to_branch)
        if len(from_commits) == 0:
            print("Nothing to do")
            self.checkout(orig_branch)
            return
        for commit in reversed(from_commits):
            self.cherry_pick(commit)
        self.checkout(orig_branch)

    def push(self, branch: str) -> None:
        self._run_git("push", self.remote, branch)

    def head_hash(self) -> str:
        return self._run_git("show-ref", "--hash", "HEAD").strip()

    def remote_url(self) -> str:
        return self._run_git("remote", "get-url", self.remote)

    def gh_owner_and_name(self) -> Tuple[str, str]:
        url = os.getenv("GIT_REMOTE_URL", None)
        if url is None:
            url = self.remote_url()
        rc = RE_GITHUB_URL_MATCH.match(url)
        if rc is None:
            raise RuntimeError(f"Unexpected url format {url}")
        return cast(Tuple[str, str], rc.groups())

    def commit_message(self, ref: str) -> str:
        return self._run_git("log", "-1", "--format=%B", ref)

    def amend_commit_message(self, msg: str) -> None:
        self._run_git("commit", "--amend", "-m", msg)


class PeekableIterator(Iterator[str]):
    def __init__(self, val: str) -> None:
        self._val = val
        self._idx = -1

    def peek(self) -> Optional[str]:
        if self._idx + 1 >= len(self._val):
            return None
        return self._val[self._idx + 1]

    def __iter__(self) -> "PeekableIterator":
        return self

    def __next__(self) -> str:
        rc = self.peek()
        if rc is None:
            raise StopIteration
        self._idx += 1
        return rc


def patterns_to_regex(allowed_patterns: List[str]) -> Any:
    """
    pattern is glob-like, i.e. the only special sequences it has are:
      - ? - matches single character
      - * - matches any non-folder separator characters
      - ** - matches any characters
      Assuming that patterns are free of braces and backslashes
      the only character that needs to be escaped are dot and plus
    """
    rc = "("
    for idx, pattern in enumerate(allowed_patterns):
        if idx > 0:
            rc += "|"
        pattern_ = PeekableIterator(pattern)
        assert not any(c in pattern for c in "{}()[]\\")
        for c in pattern_:
            if c == ".":
                rc += "\\."
            elif c == "+":
                rc += "\\+"
            elif c == "*":
                if pattern_.peek() == "*":
                    next(pattern_)
                    rc += ".+"
                else:
                    rc += "[^/]+"
            else:
                rc += c
    rc += ")"
    return re.compile(rc)
