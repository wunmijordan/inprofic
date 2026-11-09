"""Common list hierarchy for text accompanying shared snapshots."""


def bullet(text, *, nested=False, level=None):
    depth = max(0, level if level is not None else int(nested))
    marker = '•' if depth == 0 else '◦' if depth == 1 else '▪'
    return '  ' * depth + marker + ' ' + text.strip()


def render_lines(lines):
    """Normalize existing list markers while preserving headings and blank lines."""
    result = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(('• ', '◦ ', '▪ ')):
            result.append(bullet(stripped[2:], level=(len(line)-len(stripped))//2))
        else:
            result.append(line)
    return '\n'.join(result)
