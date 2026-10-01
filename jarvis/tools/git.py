"""Git tool wrappers (argv lists — no shell, no quoting)."""
from ..constants import GIT_LOG_DEFAULT_COUNT, DEFAULT_BASH_TIMEOUT
from .shell import run_argv


def git_status(): return run_argv(["git", "--no-pager", "status", "-sb"], DEFAULT_BASH_TIMEOUT)
def git_diff(path: str = "", staged=None):
    # staged=True -> only staged (git add'ed) changes; staged=False -> only unstaged.
    # Default (None) -> diff against HEAD so staged AND unstaged changes both show.
    flag = "--cached" if staged is True else ("HEAD" if staged is None else "")
    argv = ["git", "--no-pager", "diff"] + ([flag] if flag else [])
    if path:
        argv += ["--", path]
    return run_argv(argv, 15)
def git_log(n: int = GIT_LOG_DEFAULT_COUNT): return run_argv(["git", "--no-pager", "log", "--oneline", "-n", str(int(n))], DEFAULT_BASH_TIMEOUT)
