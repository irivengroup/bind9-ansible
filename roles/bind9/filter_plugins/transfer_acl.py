"""Inspect direct transfer wildcard entries in native named-checkconf -p output.

The native validator remains responsible for BIND syntax and ACL semantics.
This guard rejects direct `any` entries at every allow-transfer scope, including
entries following nested ACLs, without mistaking a key named "any" for a rule.
"""
import re


class TransferAclGuard:
    """Read canonical tokens with bounded input and nesting, without evaluation."""

    MAX_OUTPUT = 16777216
    MAX_DEPTH = 256
    TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{};]|[^\s{};"]+')

    @classmethod
    def has_no_direct_wildcard(cls, output):
        """Return False for a direct wildcard or incomplete canonical evidence.

        Nested entries belong to their own brace level. Retain only two token
        classifications per direct entry; opaque names and key material are
        never copied into parser state, exceptions or diagnostics.
        """
        if not isinstance(output, str) or len(output) > cls.MAX_OUTPUT or not output.strip() or '\x00' in output:
            return False
        stack, pending, position = [], False, 0
        for match in cls.TOKEN.finditer(output):
            if output[position:match.start()].strip():
                return False
            position = match.end()
            token = match.group()
            if pending:
                if token != '{' or len(stack) >= cls.MAX_DEPTH:  # nosec B105  # BIND lexical delimiter.
                    return False
                stack.append([])
                pending = False
            elif token == '{':  # nosec B105  # BIND lexical delimiter.
                if len(stack) >= cls.MAX_DEPTH:
                    return False
                if stack and stack[-1] is not None and len(stack[-1]) < 2:
                    stack[-1].append('group')
                stack.append(None)
            elif token == '}':  # nosec B105  # BIND lexical delimiter.
                if not stack or stack[-1]:
                    return False
                stack.pop()
            elif token == ';':  # nosec B105  # BIND lexical delimiter.
                if stack and stack[-1] is not None:
                    if stack[-1] == ['any']:
                        return False
                    stack[-1].clear()
            elif token.lower() == 'allow-transfer':
                pending = True
            elif stack and stack[-1] is not None and len(stack[-1]) < 2:
                stack[-1].append('any' if token.lower() in ('any', '"any"') else 'other')
        return not (output[position:].strip() or pending or stack)


class FilterModule:
    """Expose the guard to the existing deployment assertion."""

    def filters(self):
        return {'transfer_acl_has_no_direct_wildcard': TransferAclGuard.has_no_direct_wildcard}
