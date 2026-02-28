"""Parse unified git diffs into per-file changed line collections."""

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FileDiff:
    filename: str
    added_lines: List[str] = field(default_factory=list)
    # Line numbers in the new file corresponding to each entry in added_lines.
    added_line_numbers: List[int] = field(default_factory=list)
    removed_lines: List[str] = field(default_factory=list)
    # Reconstructed full content of the new file version (context + added lines)
    new_content: str = ""


# Matches the hunk header, e.g. "@@ -1,16 +3,11 @@"
# Captures the new-file start line from the "+" side.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(diff_text: str) -> List[FileDiff]:
    """Parse a unified diff string and return one FileDiff per changed SQL file.

    Only added lines (+) and context lines are used to reconstruct new_content.
    Removed lines (-) are stored for reference but not included in new_content.
    """
    files: List[FileDiff] = []
    current_file: Optional[FileDiff] = None
    new_content_lines: List[str] = []
    new_line_no: int = 0  # current line number in the new file

    for line in diff_text.splitlines():
        # New file section starts
        if line.startswith("diff --git "):
            if current_file is not None:
                current_file.new_content = "\n".join(new_content_lines)
                files.append(current_file)
            current_file = None
            new_content_lines = []
            new_line_no = 0

        elif line.startswith("+++ b/"):
            filename = line[6:]
            if filename != "/dev/null":
                current_file = FileDiff(filename=filename)

        elif line.startswith("--- ") or line.startswith("index "):
            pass  # skip diff headers

        elif line.startswith("@@"):
            # Parse the hunk header to find where the new-file block starts.
            m = _HUNK_RE.match(line)
            if m:
                new_line_no = int(m.group(1))

        elif current_file is not None:
            if line.startswith("+"):
                added = line[1:]
                current_file.added_lines.append(added)
                current_file.added_line_numbers.append(new_line_no)
                new_content_lines.append(added)
                new_line_no += 1
            elif line.startswith("-"):
                current_file.removed_lines.append(line[1:])
                # Removed lines don't exist in the new file — don't increment.
            elif line.startswith("\\"):
                pass  # "\ No newline at end of file"
            else:
                # Context line (starts with space, or is truly empty)
                context = line[1:] if line.startswith(" ") else line
                new_content_lines.append(context)
                new_line_no += 1

    # Flush the last file
    if current_file is not None:
        current_file.new_content = "\n".join(new_content_lines)
        files.append(current_file)

    # Only return SQL files
    return [f for f in files if f.filename.endswith(".sql")]
