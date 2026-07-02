"""Interactive prompt helpers shared across CLI flows."""
from __future__ import annotations


def ask_yes_no(message: str, *, default: bool = True, style=None) -> bool:
    """Ask an inline yes/no question that only submits on Enter.

    InquirerPy's confirm prompt accepts a single "y" or "n" as submission.
    For Metis setup flows, that can accidentally answer the next prompt when
    users are still typing. This uses cooked terminal input directly so invalid
    answers do not get stuck inside prompt_toolkit's edit buffer.
    """
    import sys

    suffix = " [Y/n]" if default else " [y/N]"
    valid_yes = {"y", "yes"}
    valid_no = {"n", "no"}

    if not sys.stdin.isatty():
        answer = _read_noninteractive_prompt(f"{message}{suffix}", default=default, style=style)
        normalized = (answer or "").strip().lower()
        if normalized == "":
            return default
        return normalized in valid_yes

    while True:
        answer = _read_prompt_line(f"{message}{suffix} ").strip().lower()
        if answer == "":
            return default
        if answer in valid_yes:
            return True
        if answer in valid_no:
            return False
        sys.stdout.write("Please enter y/yes or n/no, then press Enter.\n")
        sys.stdout.flush()


def _read_noninteractive_prompt(message: str, *, default: bool, style=None) -> str:
    """Prompt through InquirerPy in tests/non-tty contexts where stdin is blocked."""
    from InquirerPy import inquirer

    return inquirer.text(
        message=message,
        default="",
        validate=lambda value: value.strip().lower() in ("", "y", "yes", "n", "no"),
        invalid_message="Please enter y/yes or n/no, then press Enter.",
        style=style,
    ).execute()


def _read_prompt_line(prompt: str) -> str:
    """Read a single line in cooked mode, even after prompt_toolkit prompts."""
    import sys

    try:
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = old[:]
        new[3] |= termios.ICANON | termios.ECHO
        new[0] |= getattr(termios, "ICRNL", 0)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            return sys.stdin.readline()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        return input(prompt)
