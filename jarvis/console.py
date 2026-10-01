"""Shared rich Console singleton and a safe import for anthropic/rich deps."""
import pathlib, sys

try:
    from anthropic import Anthropic, APIStatusError, APIConnectionError, RateLimitError
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich.table import Table
    from rich.syntax import Syntax
    from rich.live import Live
    from rich.spinner import Spinner
except ModuleNotFoundError:
    root = pathlib.Path(__file__).resolve().parent.parent
    script = root / "agent.py"
    if sys.platform == "win32":
        venv_python = root / ".venv" / "Scripts" / "python.exe"
        activate = r".venv\Scripts\activate"
        system_python = "Your `python` is a system-wide interpreter"
    else:
        venv_python = root / ".venv" / "bin" / "python"
        activate = "source .venv/bin/activate"
        system_python = "Your `python3` is the system (e.g. Homebrew) interpreter"
    print(
        f"{system_python}. "
        "`anthropic` and `rich` are installed only in this project's `.venv`, "
        "so that interpreter cannot import them.\n\n"
        "Run:\n\n"
        f"  {venv_python} {script}\n\n"
        "Or from the project folder:\n\n"
        f"  cd {root}\n"
        f"  {activate}\n"
        f"  python {script.name}\n",
        file=sys.stderr,
    )
    raise SystemExit(1)

console = Console()


class HarnessAPIError(Exception):
    """API failure after user-facing message was printed; do not exit the TUI."""
