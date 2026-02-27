"""Parse unified git diffs into per-file changed line collections."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class FileDiff:
    filename: str
    added_lines: List[str] = field(default_factory=list)
    removed_lines: List[str] = field(default_factory=list)
    # Reconstructed full content of the new file version (context + added lines)
    new_content: str = ""


def parse_diff(diff_text: str) -> List[FileDiff]:
    """Parse a unified diff string and return one FileDiff per changed SQL file.

    Only added lines (+) and context lines are used to reconstruct new_content.
    Removed lines (-) are stored for reference but not included in new_content.
    """
    files: List[FileDiff] = []
    current_file: FileDiff | None = None
    new_content_lines: List[str] = []

    for line in diff_text.splitlines():
        # New file section starts
        if line.startswith("diff --git "):
            if current_file is not None:
                current_file.new_content = "\n".join(new_content_lines)
                files.append(current_file)
            current_file = None
            new_content_lines = []

        elif line.startswith("+++ b/"):
            filename = line[6:]
            if filename != "/dev/null":
                current_file = FileDiff(filename=filename)

        elif line.startswith("--- ") or line.startswith("@@ ") or line.startswith("index "):
            pass  # skip diff headers / hunk headers

        elif current_file is not None:
            if line.startswith("+"):
                added = line[1:]
                current_file.added_lines.append(added)
                new_content_lines.append(added)
            elif line.startswith("-"):
                current_file.removed_lines.append(line[1:])
            elif line.startswith("\\"):
                pass  # "\ No newline at end of file"
            else:
                # Context line (starts with space, or is truly empty)
                context = line[1:] if line.startswith(" ") else line
                new_content_lines.append(context)

    # Flush the last file
    if current_file is not None:
        current_file.new_content = "\n".join(new_content_lines)
        files.append(current_file)

    # Only return SQL files
    return [f for f in files if f.filename.endswith(".sql")]
