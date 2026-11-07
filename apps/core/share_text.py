"""Common list hierarchy for text accompanying shared snapshots."""


def bullet(text, *, nested=False):
    return ('  ◦ ' if nested else '• ') + text.strip()


def render_lines(lines):
    """Normalize existing list markers while preserving headings and blank lines."""
    result = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(('• ', '◦ ')):
            result.append(bullet(stripped[2:], nested=len(line) != len(stripped)))
        else:
            result.append(line)
    return '\n'.join(result)
