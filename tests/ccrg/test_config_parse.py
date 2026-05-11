#!/usr/bin/env python
"""Test config parsing."""

with open('.gateway.json', 'r', encoding='utf-8') as f:
    content = f.read()

def _strip_comments(text):
    lines = []
    for line in text.splitlines():
        stripped = _remove_comment(line)
        if stripped:
            lines.append(stripped)
    return '\n'.join(lines)

def _remove_comment(line):
    result = []
    i = 0
    in_string = False
    escape_next = False

    while i < len(line):
        char = line[i]

        if escape_next:
            result.append(char)
            escape_next = False
            i += 1
            continue

        if char == '\\' and in_string:
            result.append(char)
            escape_next = True
            i += 1
            continue

        if char == '"' and not in_string:
            in_string = True
            result.append(char)
            i += 1
            continue

        if char == '"' and in_string:
            in_string = False
            result.append(char)
            i += 1
            continue

        if char == '/' and i + 1 < len(line) and line[i + 1] == '/' and not in_string:
            break

        result.append(char)
        i += 1

    return ''.join(result).rstrip()

stripped = _strip_comments(content)

import json
try:
    config = json.loads(stripped)
    print('OK: JSON parses correctly')
    print('Providers:', list(config.get('providers', {}).keys()))
except json.JSONDecodeError as e:
    print(f'Error at position {e.pos}: {e.msg}')