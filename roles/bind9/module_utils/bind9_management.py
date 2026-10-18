"""Targeted static BIND maintenance for Python 3.6+, using only the stdlib.

Configuration syntax is retained as concrete source spans. We never regenerate
unrelated clauses. Writes require a revision of the complete static input set,
an exclusive host lease, native BIND validation and an immediate revision check.
Dynamic zones and include/generate directives inside a changed zone are refused.
"""
import contextlib
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
# Native BIND tools use internal argv, never a shell.
import subprocess  # nosec B404
import tempfile
import time


class ManagementError(ValueError):
    """A safe, operator-facing refusal; never include configuration secrets."""


class VerifiedIO:
    """Refuse short writes and verify persisted staging bytes before publication."""

    @staticmethod
    def write(stream, data):
        if stream.write(data) != len(data):
            raise ManagementError('Incomplete private file write; no publication is permitted')
        stream.flush()
        os.fsync(stream.fileno())
        if os.fstat(stream.fileno()).st_size != len(data):
            raise ManagementError('Private file size differs from the intended bytes')
        stream.seek(0)
        if stream.read(len(data) + 1) != data:
            raise ManagementError('Private file readback differs from the intended bytes')

    @staticmethod
    def directory(path):
        descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def create(cls, path, data, mode=0o600):
        descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(descriptor, 'w+b') as stream:
            os.fchmod(stream.fileno(), mode)
            cls.write(stream, data)
        cls.directory(path.parent)


class NativeCommand:
    """Capture complete bounded output and terminate the owned process group on failure."""

    LIMIT = 1024 * 1024
    TIMEOUT = 60

    @classmethod
    def run(cls, argv):
        # All callers select internal tools and supply argv, never shell source.
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   close_fds=True, shell=False)  # nosec B603
        deadline, result = time.monotonic() + cls.TIMEOUT, bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ManagementError('Native command timed out: ' + Path(argv[0]).name)
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, min(65536, cls.LIMIT + 1 - len(result)))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        result.extend(chunk)
                        if len(result) > cls.LIMIT:
                            raise ManagementError('Native command output exceeds the safety bound')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ManagementError('Native command timed out: ' + Path(argv[0]).name)
            try:
                code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as error:
                raise ManagementError('Native command timed out: ' + Path(argv[0]).name) from error
            if code:
                raise ManagementError('Native validation/reload failed: ' + Path(argv[0]).name)
            try:
                return bytes(result).decode('utf-8', errors='strict')
            except UnicodeDecodeError as error:
                raise ManagementError('Native command returned invalid UTF-8 output') from error
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The complete owned group has already exited.
            process.wait(timeout=5)
            raise
        finally:
            process.stdout.close()


class Token:
    """One non-comment token with offsets into the original source."""

    def __init__(self, text, start, end):
        self.text, self.start, self.end = text, start, end

    @property
    def value(self):
        return self.text[1:-1] if self.text.startswith('"') else self.text


class Clause:
    """A BIND statement, including its terminating semicolon."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.start, self.end = tokens[0].start, tokens[-1].end
        self.name = tokens[0].value.lower()
        self.open = next((n for n, token in enumerate(tokens) if token.text == '{'), None)

    @property
    def header(self):
        return self.tokens[:self.open] if self.open is not None else self.tokens[:-1]

    def children(self):
        if self.open is None:
            raise ManagementError('Expected a block statement')
        return Syntax.statements(self.tokens[self.open + 1:-2])

    def simple_block(self):
        """True only when the first block encloses the complete statement."""
        if self.open is None:
            return False
        depth = 0
        for index in range(self.open, len(self.tokens)):
            depth += (self.tokens[index].text == '{') - (self.tokens[index].text == '}')
            if depth == 0:
                return index == len(self.tokens) - 2
        return False

    def scalar(self):
        if self.open is not None or len(self.tokens) != 3:
            raise ManagementError('Expected one scalar value for ' + self.name)
        return self.tokens[1].value


class Syntax:
    """Tokenize BIND comments, quoted strings and balanced statement groups."""

    PATTERN = re.compile(r'\s+|//[^\n]*|\#[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|[{};]|(?:(?!//|/\*)[^\s{};"#])+', re.S)
    MAX_NESTING = 64
    MAX_TOKENS = 250000

    def __init__(self, text):
        self.text = text
        tokens, position = [], 0
        for match in self.PATTERN.finditer(text):
            if match.start() != position:
                raise ManagementError('Unsupported or unclosed BIND token')
            position = match.end()
            value = match.group()
            if value.isspace() or value.startswith(('//', '#', '/*')):
                continue
            if len(tokens) >= self.MAX_TOKENS:
                raise ManagementError('BIND token count exceeds the supported safety bound')
            tokens.append(Token(value, match.start(), match.end()))
        if position != len(text):
            raise ManagementError('Unclosed BIND string or comment')
        self.token_count = len(tokens)
        self.clauses = self.statements(tokens)

    @staticmethod
    def statements(tokens):
        result, group, depth = [], [], 0
        for token in tokens:
            group.append(token)
            if token.text == '{':
                depth += 1
                if depth > Syntax.MAX_NESTING:
                    raise ManagementError('BIND nesting exceeds the supported safety bound')
            elif token.text == '}':
                depth -= 1
                if depth < 0:
                    raise ManagementError('Unbalanced BIND block')
            elif token.text == ';' and depth == 0:
                result.append(Clause(group))
                group = []
        if depth or group:
            raise ManagementError('Incomplete BIND statement')
        return result

    @staticmethod
    def one(clauses, name, required=True):
        found = [clause for clause in clauses if clause.name == name]
        if len(found) > 1 or (required and not found):
            raise ManagementError('Missing or ambiguous ' + name + ' statement')
        return found[0] if found else None

    def replace(self, clause, replacement):
        return self.text[:clause.start] + replacement + self.text[clause.end:]


class SafeFiles:
    """Read bounded regular files, refusing links and writable/untrusted parents."""

    LIMIT = 16 * 1024 * 1024

    def __init__(self, root='/'):
        self.root = Path(root)
        self.observations = {}

    @staticmethod
    def stamp(info):
        """Detect replacement, in-place writes and permission changes, even at equal bytes."""
        return (info.st_dev, info.st_ino, info.st_size, info.st_uid, info.st_gid,
                info.st_mode, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)

    def path(self, virtual):
        path = Path(virtual)
        if not path.is_absolute() or '..' in path.parts:
            raise ManagementError('Only canonical absolute paths are accepted')
        if not (str(path).startswith('/etc/') or str(path).startswith('/var/named/')):
            raise ManagementError('Configuration assets must remain under /etc or /var/named')
        physical = self.root.joinpath(*path.parts[1:])
        cursor = self.root
        for part in path.parts[1:-1]:
            cursor = cursor / part
            info = cursor.lstat()
            # EL8 ships /var/named as root:named 1770. Its sticky bit
            # protects root-owned entries; do not chmod native server state.
            native_sticky = cursor == self.root / 'var/named' and stat.S_IMODE(info.st_mode) == 0o1770
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or (info.st_mode & 0o022 and not native_sticky):
                raise ManagementError('Unsafe configuration ancestor: ' + str(path.parent))
        return physical

    def read(self, virtual):
        path = self.path(virtual)
        if virtual == '/etc/crypto-policies/back-ends/bind.config' and path.is_symlink():
            # This one vendor-managed link is part of native RHEL crypto policy.
            target = path.resolve(strict=True)
            relative = '/' + target.relative_to(self.root.resolve()).as_posix()
            if not relative.startswith(('/etc/crypto-policies/', '/usr/share/crypto-policies/')):
                raise ManagementError('Unsafe vendor crypto-policy link target')
            cursor = self.root
            for part in Path(relative).parts[1:-1]:
                cursor = cursor / part
                info = cursor.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                    raise ManagementError('Unsafe vendor crypto-policy ancestor')
            path = target
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or info.st_mode & 0o022:
                raise ManagementError('Unsafe configuration file: ' + virtual)
            if info.st_size > self.LIMIT:
                raise ManagementError('Configuration file exceeds the supported size bound')
            data = stream.read(self.LIMIT + 1)
            if len(data) > self.LIMIT:
                raise ManagementError('Configuration file exceeds the supported size bound')
            if len(data) != info.st_size:
                raise ManagementError('Incomplete static file read; refusing the snapshot')
            try:
                data.decode('utf-8', errors='strict')
            except UnicodeDecodeError as error:
                raise ManagementError('Static BIND asset is not valid UTF-8 text') from error
            if b'\x00' in data:
                raise ManagementError('Static BIND asset contains a NUL byte')
            observed = self.stamp(info)
            if observed != self.stamp(os.fstat(stream.fileno())) or observed != self.stamp(path.lstat()):
                raise ManagementError('Configuration changed during a static file read')
            self.observations[virtual] = observed
        return data


class BoundedFiles(SafeFiles):
    """Cap aggregate discovery cost for catalog and multi-resource workflows."""

    MAX_BYTES = 64 * 1024 * 1024
    MAX_FILES = 512

    def __init__(self, root='/'):
        super(BoundedFiles, self).__init__(root)
        self.sizes = {}

    def read(self, virtual):
        data = super(BoundedFiles, self).read(virtual)
        self.sizes[virtual] = len(data)
        if len(self.sizes) > self.MAX_FILES or sum(self.sizes.values()) > self.MAX_BYTES:
            raise ManagementError('Static discovery exceeds the 512 asset / 64 MiB aggregate bound')
        return data


class Configuration:
    """Discover the static include graph and master zones without rendering them."""

    MAX_TOKENS = 500000

    def __init__(self, files):
        self.files, self.contents, self.configs, self.zones = files, {}, {}, {}
        self.token_count = 0
        self.directory = '/var/named'
        self.load('/etc/named.conf', set())
        root = self.configs['/etc/named.conf']
        options = Syntax.one(root.clauses, 'options')
        directory = Syntax.one(options.children(), 'directory', False)
        if directory:
            self.directory = directory.scalar()
        if self.directory != '/var/named':
            raise ManagementError('Management requires the deployed /var/named directory')
        anchor = Syntax.one(options.children(), 'bindkeys-file', False)
        if anchor:
            path = anchor.scalar()
            self.contents[path] = files.read(path)
        for filename, syntax in self.configs.items():
            for clause in syntax.clauses:
                if clause.name == 'view':
                    raise ManagementError('This project manages one namespace without views')
                if clause.name != 'zone':
                    continue
                header = clause.header
                if len(header) not in (2, 3) or (len(header) == 3 and header[2].value.upper() != 'IN'):
                    raise ManagementError('Only class IN zones are supported')
                domain = canonical_name(header[1].value)
                if domain in self.zones:
                    raise ManagementError('Duplicate zone declaration')
                children = clause.children()
                kind = Syntax.one(children, 'type').scalar()
                file_clause = Syntax.one(children, 'file', False)
                zone = {'domain': domain, 'kind': kind, 'clause': clause, 'config': filename}
                if file_clause:
                    path = file_clause.scalar()
                    zone['file'] = path if path.startswith('/') else self.directory + '/' + path
                self.zones[domain] = zone
                if kind in ('master', 'primary'):
                    if 'file' not in zone:
                        raise ManagementError('Master zone without a static file')
                    for forbidden in ('update-policy', 'inline-signing', 'database', 'dlz'):
                        if Syntax.one(children, forbidden, False):
                            raise ManagementError('Dynamic or externally managed master zones are not supported')
                    update = Syntax.one(children, 'allow-update', False)
                    if update and [token.value for token in update.tokens[2:-2]] != ['none', ';']:
                        raise ManagementError('Dynamic master zones must be managed through their own update channel')
                    self.contents[zone['file']] = files.read(zone['file'])
                    if os.path.lexists(str(files.root) + zone['file'] + '.jnl'):
                        raise ManagementError('A master zone journal is present; reconcile it before static maintenance')
        self.observations = {name: files.observations[name] for name in self.contents
                             if name in files.observations}
        # Do not return a catalog/checksum assembled from different live states.
        for name, observed in self.observations.items():
            if files.read(name) != self.contents[name] or files.observations[name] != observed:
                raise ManagementError('Configuration changed while observing the static graph')

    def load(self, path, visiting):
        if path in visiting:
            raise ManagementError('Circular BIND include graph')
        if path in self.configs:
            return
        if len(self.configs) >= 128:
            raise ManagementError('Too many BIND includes')
        data = self.files.read(path)
        syntax = Syntax(data.decode('utf-8'))
        self.token_count += syntax.token_count
        if self.token_count > self.MAX_TOKENS:
            raise ManagementError('Static configuration token count exceeds the supported safety bound')
        self.contents[path], self.configs[path] = data, syntax
        visiting = visiting | {path}
        # Includes can occur inside options, e.g. the vendor crypto policy.
        def walk(clauses):
            for clause in clauses:
                if clause.name == 'include':
                    include = clause.scalar()
                    if not include.startswith('/'):
                        include = '/var/named/' + include
                    self.load(include, visiting)
                elif clause.simple_block():
                    walk(clause.children())
        walk(syntax.clauses)

    def revision(self):
        manifest = {name: hashlib.sha256(data).hexdigest() for name, data in self.contents.items()}
        return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode('utf-8')).hexdigest()


def canonical_name(value, origin='.'):
    """Normalize ordinary DNS presentation names; reject ambiguous escapes."""
    if not isinstance(value, str) or not value or '\\' in value or any(c.isspace() for c in value):
        raise ManagementError('Use an ordinary DNS name without presentation escapes')
    if value == '@':
        return origin
    name = value.lower() if value.endswith('.') else value.lower() + ('.' if origin == '.' else '.' + origin)
    if len(name) > 254 or any(not label or len(label) > 63 or not re.fullmatch(r'[a-z0-9_*\-]+', label) for label in name.rstrip('.').split('.')):
        if name != '.':
            raise ManagementError('Invalid DNS name')
    return name


class ZoneDocument:
    """Retain static zone source, logical RR spans and inherited owner/origin."""

    TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|(?:\\.|[^\s();])+|[()]')
    MAX_RECORDS = 100000

    def __init__(self, text, domain):
        self.text, self.domain, self.records = text, domain, []
        origin, owner, ttl = domain, None, None
        for start, end in self.lines(text):
            source = text[start:end]
            clean = self.without_comments(source)
            tokens = [Token(m.group(), start + m.start(), start + m.end()) for m in self.TOKEN.finditer(clean) if m.group() not in ('(', ')')]
            if not tokens:
                continue
            values = [token.text for token in tokens]
            if values[0].upper() == '$ORIGIN':
                if len(values) != 2:
                    raise ManagementError('Invalid $ORIGIN')
                origin = canonical_name(values[1], origin)
                continue
            if values[0].upper() == '$TTL':
                ttl = values[1]
                continue
            if values[0].startswith('$'):
                raise ManagementError('Static management refuses $INCLUDE/$GENERATE or unknown zone directives')
            index = 0
            if not source[:1].isspace():
                owner = canonical_name(values[0], origin)
                index += 1
            if not owner:
                raise ManagementError('Missing inherited record owner')
            record_ttl = ttl
            for _ in range(2):
                if index < len(values) and values[index].upper() == 'IN':
                    index += 1
                elif index < len(values) and re.fullmatch(r'[0-9]+[WDHMSwdhms0-9]*', values[index]):
                    record_ttl = values[index]
                    index += 1
            if index >= len(values):
                raise ManagementError('Missing record type')
            kind = values[index].upper()
            if len(self.records) >= self.MAX_RECORDS:
                raise ManagementError('Zone record count exceeds the supported safety bound')
            self.records.append({'name': owner, 'type': kind, 'ttl': record_ttl,
                                 'tokens': tokens[index + 1:], 'start': start, 'end': end})

    @staticmethod
    def without_comments(text):
        """Mask comments without shifting offsets inside a multiline SOA."""
        result, quoted, escaped, comment = [], False, False, False
        for char in text:
            if comment:
                result.append('\n' if char == '\n' else ' ')
                if char == '\n':
                    comment = False
            else:
                result.append(' ' if char == ';' and not quoted and not escaped else char)
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = not quoted
                elif char == ';' and not quoted:
                    comment = True
        return ''.join(result)

    @staticmethod
    def lines(text):
        start, depth, quoted, escaped, comment = 0, 0, False, False, False
        for index, char in enumerate(text):
            if comment:
                if char != '\n':
                    continue
                comment = False
            elif escaped:
                escaped = False
                continue
            elif char == '\\':
                escaped = True
                continue
            elif char == '"':
                quoted = not quoted
            elif not quoted:
                if char == ';':
                    comment = True
                elif char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
            if char == '\n' and not quoted and depth == 0:
                if text[start:index].lstrip().startswith(';') or not text[start:index].strip():
                    start = index + 1
                    continue
                yield start, index + 1
                start = index + 1
        if quoted or depth or escaped:
            raise ManagementError('Incomplete zone-file record')
        if text[start:].strip() and not text[start:].lstrip().startswith(';'):
            yield start, len(text)

    def rrset(self, owner, kind):
        return [record for record in self.records if record['name'] == owner and record['type'] == kind]

    def serial(self):
        records = self.rrset(self.domain, 'SOA')
        if len(records) != 1 or len(records[0]['tokens']) != 7:
            raise ManagementError('Expected exactly one ordinary apex SOA')
        token = records[0]['tokens'][2]
        if not token.text.isdigit() or not 0 <= int(token.text) <= 4294967295:
            raise ManagementError('Invalid SOA serial')
        return token

    def bump(self):
        token = self.serial()
        value = int(token.text) + 1 if int(token.text) < 4294967295 else 1
        return self.text[:token.start] + str(value) + self.text[token.end:]

    def change(self, action, owner, kind, values, ttl=None):
        old = self.rrset(owner, kind)
        if action == 'create' and any(record['name'] == owner for record in self.records):
            raise ManagementError('Record owner already exists; create never extends or replaces an existing name')
        if action in ('update', 'delete') and not old:
            raise ManagementError('RRset does not exist')
        rendered = ''
        if action != 'delete':
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() or '\n' in v or '\r' in v for v in values):
                raise ManagementError('values must be a nonempty list of one-line RDATA strings')
            for value in values:
                # A semicolon outside a quoted string would hide trailing data.
                if any(token in re.sub(r'"(?:\\.|[^"\\])*"', '', value) for token in (';', '(', ')', '$')):
                    raise ManagementError('Unsafe RDATA presentation')
            if ttl is None:
                ttl = old[0]['ttl'] if old else None
            if ttl is not None and (isinstance(ttl, bool) or not re.fullmatch(r'[0-9]+[WDHMSwdhms0-9]*', str(ttl))):
                raise ManagementError('Invalid record TTL')
            rendered = ''.join(owner + (' ' + str(ttl) if ttl is not None else '') + ' IN ' + kind + ' ' + value.strip() + '\n' for value in values)
            if old and [" ".join(token.text for token in r['tokens']) for r in old] == [v.strip() for v in values] and all(str(r['ttl']) == str(ttl) for r in old):
                return self.text
        text = self.text
        for number, record in reversed(list(enumerate(old))):
            text = text[:record['start']] + (rendered if number == 0 else '') + text[record['end']:]
        if not old:
            text += ('\n' if text and not text.endswith('\n') else '') + rendered
        after = ZoneDocument(text, self.domain)
        def unaffected(records):
            return sorted((r['name'], r['type'], r['ttl'] or '', tuple(t.text for t in r['tokens']))
                          for r in records if (r['name'], r['type']) != (owner, kind))
        if unaffected(self.records) != unaffected(after.records):
            raise ManagementError('This edit would change inherited owners/TTL of another record; make those owners explicit first')
        return after.bump()


class DnsReply:
    """Match a dig answer to the exact question, status and expected SOA.

    A CNAME target or an unrelated answer with the same address/serial cannot
    prove that the requested local zone is healthy.
    """

    def __init__(self, text):
        statuses = re.findall(r'^;; [^\n]*\bstatus: ([A-Z]+)(?:,|\s*$)', text, re.M)
        flags = re.findall(r'^;; flags: ([^;\n]*);', text, re.M)
        self.success = statuses == ['NOERROR'] and len(flags) <= 1 and not any('tc' in item.split() for item in flags)
        self.authoritative = bool(re.search(r'^;; flags: [^;\n]*\baa\b', text, re.M))
        self.answers = []
        for line in text.splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[1].isdigit() and fields[2].upper() == 'IN':
                self.answers.append((fields[0].lower(), fields[3].upper(), fields[4:]))
        counts = re.findall(r'^;; flags: [^\n]*\bANSWER:\s*([0-9]+)', text, re.M)
        if counts and (len(counts) != 1 or int(counts[0]) != len(self.answers)):
            self.success = False

    def matches(self, name, kind, expected):
        """Require the owner/type and, for local SOAs, authority and serial."""
        if not self.success:
            return False
        for owner, record_type, data in self.answers:
            if owner != name.lower() or record_type != kind:
                continue
            if kind == 'A' and data == [expected]:
                return True
            if kind == 'SOA' and len(data) == 7 and data[2].isdigit():
                if expected is None or (self.authoritative and (expected == '*' or data[2] == expected)):
                    return True
        return False


class Manager:
    """One compare-and-swap operation, preserving all other concrete source."""

    PROTECTED_ZONES = {'.', 'localhost.', 'localhost.localdomain.', '0.in-addr.arpa.',
                       '127.in-addr.arpa.', '255.in-addr.arpa.',
                       ipaddress.IPv6Address('::1').reverse_pointer + '.'}
    # These settings own paths/runtime hooks. They must change through a coordinated
    # deployment, not a one-clause maintenance operation with inconsistent assets.
    PROTECTED_OPTIONS = {'directory', 'pid-file', 'session-keyfile', 'dnssec-validation',
                         'bindkeys-file', 'managed-keys-directory', 'allow-transfer', 'allow-update',
                         'allow-query', 'allow-query-on', 'allow-recursion', 'allow-recursion-on',
                         'allow-query-cache', 'allow-query-cache-on', 'listen-on', 'listen-on-v6',
                         'transfer-source', 'notify-source'}
    MAX_REQUEST_BYTES = 256 * 1024

    def __init__(self, request, context, root='/', runner=None, check=False):
        if not isinstance(request, dict) or any(not isinstance(key, str) for key in request):
            raise ManagementError('Management request must be a mapping with string keys')
        try:
            encoded = json.dumps(request, sort_keys=True, allow_nan=False).encode('utf-8')
            request = json.loads(encoded.decode('utf-8'))
        except (TypeError, ValueError, RecursionError) as error:
            raise ManagementError('Management request must be bounded ordinary JSON data') from error
        if len(encoded) > self.MAX_REQUEST_BYTES:
            raise ManagementError('Management request exceeds the 256 KiB safety bound')
        self.request, self.context, self.root = request, context, Path(root)
        self.files = BoundedFiles(root)
        self.runner = runner or self.command
        self.check = check
        self.action = request.get('action')
        self.resource = request.get('resource')
        if self.action not in ('create', 'read', 'list', 'update', 'delete') or self.resource not in ('option', 'zone', 'record'):
            raise ManagementError('Use resource option/zone/record and action create/read/list/update/delete')
        allowed = {'action', 'resource', 'name', 'expected_checksum'}
        if self.action == 'list':
            allowed = {'action', 'resource', 'expected_checksum', 'offset', 'limit'}
            if self.resource == 'record':
                allowed.add('zone')
            offset, limit = request.get('offset', 0), request.get('limit', 100)
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
                raise ManagementError('Listing requires integer offset >= 0 and limit between 1 and 500')
        elif self.resource == 'option' and self.action in ('create', 'update'):
            allowed |= {'value'}
        elif self.resource == 'record':
            allowed |= {'zone', 'type'}
            if self.action in ('create', 'update'):
                allowed |= {'values', 'ttl'}
        elif self.resource == 'zone':
            allowed |= {'create': {'kind', 'forwarders', 'dnssec_validate', 'serial', 'ttl'},
                        'update': {'forwarders', 'dnssec_validate', 'settings'},
                        'delete': {'purge_data'}, 'read': set()}[self.action]
        if set(request) - allowed:
            raise ManagementError('Unknown request fields: ' + ', '.join(sorted(set(request) - allowed)))
        name = request.get('name')
        if self.action != 'list' and (not isinstance(name, str) or not name or (self.resource == 'option' and not re.fullmatch('[a-z][a-z0-9-]*', name))):
            raise ManagementError('Supply an explicit name with valid resource syntax')
        if 'purge_data' in request and not isinstance(request['purge_data'], bool):
            raise ManagementError('purge_data must be a YAML boolean')
        self.changes, self.originals = {}, {}

    @staticmethod
    def command(argv):
        # Executables are chosen by this module; validated DNS names are passed
        # as separate arguments. No request can supply a command or shell text.
        return NativeCommand.run(argv)

    def selected_zone(self):
        domain = canonical_name(self.request.get('zone', self.request.get('name', '')))
        zone = self.config.zones.get(domain)
        if not zone:
            raise ManagementError('Zone is not declared')
        return zone

    def run(self):
        if os.geteuid() != 0:
            raise ManagementError('Management requires root')
        self.config = Configuration(self.files)
        revision = self.config.revision()
        if self.action in ('read', 'list'):
            if self.action == 'list' and self.request.get('expected_checksum') not in (None, '', revision):
                raise ManagementError('Listing checksum changed; restart pagination from the current state')
            return dict(self.listing() if self.action == 'list' else self.read(), changed=False, checksum=revision)
        expected = self.request.get('expected_checksum')
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected) or expected != revision:
            raise ManagementError('Checksum missing or changed; read the current state again before writing')
        self.plan()
        self.normalize_serials()
        self.changes = {name: data for name, data in self.changes.items() if self.config.contents.get(name) != data}
        if not self.changes:
            return {'changed': False, 'checksum': revision, 'resource': self.resource, 'action': self.action}
        self.final_config = Configuration(SnapshotFiles(
            self.files, dict(self.config.contents, **self.changes)))
        self.candidate_revision = self.final_config.revision()
        self.validate()
        if self.check:
            return {'changed': True, 'checksum': revision, 'check_mode': True, 'paths': sorted(self.changes)}
        return self.commit(revision)

    def listing(self):
        """Return a deterministic page of explicit declarations or primary RRs.

        Values remain in the private module result. The public catalog carries
        identities only; Ansible prints values only on explicit operator request.
        Native implicit defaults and slave runtime files are never invented.
        """
        items, catalog = [], []
        if self.resource == 'option':
            options = Syntax.one(self.config.configs['/etc/named.conf'].clauses, 'options')
            for clause, source in self.option_declarations(options.children(), '/etc/named.conf'):
                text = self.config.contents[source].decode('utf-8')
                items.append({'name': clause.name, 'source': source,
                    'value': text[clause.tokens[0].end:clause.tokens[-1].start].strip()})
            items.sort(key=lambda item: (item['name'], item['source']))
            catalog = [{'name': item['name'], 'source': item['source']} for item in items]
        elif self.resource == 'zone':
            for name, zone in sorted(self.config.zones.items()):
                text = self.config.contents[zone['config']].decode('utf-8')
                settings = [{'name': clause.name, 'value': text[clause.tokens[0].end:clause.tokens[-1].start].strip()}
                            for clause in zone['clause'].children()]
                identity = {'name': name, 'type': zone['kind'], 'source': zone['config']}
                items.append(dict(identity, options=settings))
                catalog.append(dict(identity, option_names=[setting['name'] for setting in settings]))
        else:
            zone = self.selected_zone()
            if zone['kind'] not in ('master', 'primary'):
                raise ManagementError('List records on the primary static zone')
            document = ZoneDocument(self.config.contents[zone['file']].decode('utf-8'), zone['domain'])
            for record in document.records:
                identity = {'zone': zone['domain'], 'name': record['name'], 'type': record['type'], 'ttl': record['ttl']}
                items.append(dict(identity, value=' '.join(token.text for token in record['tokens'])))
            items.sort(key=lambda item: (item['name'], item['type'], item['value']))
            catalog = [{key: value for key, value in item.items() if key != 'value'} for item in items]
        offset, limit = self.request.get('offset', 0), self.request.get('limit', 100)
        end = offset + limit
        return {'resource': self.resource, 'action': 'list', 'total': len(items), 'offset': offset, 'limit': limit,
                'count': len(items[offset:end]), 'next_offset': end if end < len(items) else None,
                'items': items[offset:end], 'catalog': catalog[offset:end]}

    def option_declarations(self, clauses, source):
        """Expand only includes within options, retaining declaration provenance."""
        for clause in clauses:
            yield clause, source
            if clause.name == 'include':
                path = clause.scalar()
                if not path.startswith('/'):
                    path = '/var/named/' + path
                for item in self.option_declarations(self.config.configs[path].clauses, path):
                    yield item

    def read(self):
        if self.resource == 'option':
            clause, source = self.option_location(self.request.get('name', ''))
            value = self.config.contents[source].decode('utf-8')[clause.tokens[0].end:clause.tokens[-1].start].strip() if clause else None
            return {'found': clause is not None, 'value': value}
        domain = canonical_name(self.request.get('zone', self.request.get('name', '')))
        zone = self.config.zones.get(domain)
        if self.resource == 'zone':
            result = {'found': zone is not None, 'zone': domain, 'kind': zone['kind'] if zone else None}
            if zone:
                source = self.config.contents[zone['config']].decode('utf-8')
                result['declaration'] = source[zone['clause'].start:zone['clause'].end]
            if zone and zone['kind'] == 'forward':
                result['forwarders'] = []
                for server in Syntax.one(zone['clause'].children(), 'forwarders').children():
                    header = [token.value for token in server.header]
                    result['forwarders'].append({'address': header[0], 'port': int(header[2]) if len(header) == 3 and header[1] == 'port' else 53})
                marker = '// Bind9-Ansible DNSSEC-PRIVATE ' + domain.rstrip('.')
                result['dnssec_validate'] = not bool(re.search(r'(?m)^' + re.escape(marker) + r'[ \t]*$', source))
            if zone and zone['kind'] in ('master', 'primary'):
                result['serial'] = ZoneDocument(self.config.contents[zone['file']].decode('utf-8'), domain).serial().text
            return result
        if not zone or zone['kind'] not in ('master', 'primary'):
            raise ManagementError('Read records on the primary static zone')
        owner, kind = self.record_identity(domain)
        document = ZoneDocument(self.config.contents[zone['file']].decode('utf-8'), domain)
        records = document.rrset(owner, kind)
        return {'found': bool(records), 'zone': domain, 'name': owner, 'type': kind,
                'records': [{'value': ' '.join(t.text for t in r['tokens']), 'ttl': r['ttl']} for r in records],
                'values': [' '.join(t.text for t in record['tokens']) for record in records]}

    def record_identity(self, domain):
        owner = canonical_name(self.request.get('name', ''), domain)
        kind = self.request.get('type', '')
        if not isinstance(kind, str) or not re.fullmatch('[A-Za-z][A-Za-z0-9]*', kind):
            raise ManagementError('Invalid record type')
        kind = kind.upper()
        if (self.action != 'read' and kind in ('SOA', 'RRSIG', 'NSEC', 'NSEC3', 'NSEC3PARAM', 'DNSKEY', 'CDS', 'CDNSKEY')) or not (owner == domain or owner.endswith('.' + domain)):
            raise ManagementError('Record is outside the zone or owned by SOA/DNSSEC machinery')
        return owner, kind

    def plan(self):
        if self.resource == 'option':
            self.option()
        elif self.resource == 'record':
            zone = self.selected_zone()
            if zone['kind'] not in ('master', 'primary') or zone['domain'] in self.PROTECTED_ZONES:
                raise ManagementError('Record writes require a non-native primary static zone')
            owner, kind = self.record_identity(zone['domain'])
            if any(z['kind'] == 'forward' and (owner == name or owner.endswith('.' + name))
                   for name, z in self.config.zones.items()):
                raise ManagementError('A record below a forwarding cut would be hidden; manage its upstream data instead')
            document = ZoneDocument(self.config.contents[zone['file']].decode('utf-8'), zone['domain'])
            self.changes[zone['file']] = document.change(self.action, owner, kind, self.request.get('values'), self.request.get('ttl')).encode('utf-8')
        else:
            self.zone()

    def option_location(self, name):
        """Resolve explicit options through includes before any existence decision."""
        options = Syntax.one(self.config.configs['/etc/named.conf'].clauses, 'options')
        matches = [(clause, source) for clause, source in
                   self.option_declarations(options.children(), '/etc/named.conf')
                   if clause.name == name]
        if len(matches) > 1:
            raise ManagementError('Ambiguous option declarations in the live include graph')
        return matches[0] if matches else (None, None)

    def option(self):
        name = self.request.get('name', '')
        if not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9-]*', name) or name in self.PROTECTED_OPTIONS:
            raise ManagementError('Invalid or deployment-owned option; use a coordinated forced deployment for runtime/path/security ownership changes')
        syntax = self.config.configs['/etc/named.conf']
        options = Syntax.one(syntax.clauses, 'options')
        old, source = self.option_location(name)
        if (self.action == 'create') == (old is not None):
            raise ManagementError('Option already exists' if old else 'Option does not exist')
        if old and source != '/etc/named.conf':
            raise ManagementError('An option in an external/vendor include is not owned by this workflow')
        value = self.request.get('value')
        replacement = ''
        if self.action != 'delete':
            if not isinstance(value, str) or not value.strip():
                raise ManagementError('Option value must be a nonempty BIND value without its final semicolon')
            candidate = Syntax(name + ' ' + value + ';')
            if len(candidate.clauses) != 1 or candidate.clauses[0].name != name:
                raise ManagementError('One option only; additional statements are forbidden')
            replacement = name + ' ' + value.strip() + ';'
        if old:
            text = syntax.replace(old, replacement)
        else:
            end = options.tokens[-2].start
            text = syntax.text[:end] + '    ' + replacement + '\n' + syntax.text[end:]
        self.changes['/etc/named.conf'] = text.encode('utf-8')

    def zone(self):
        domain = canonical_name(self.request.get('name', ''))
        if '*' in domain or '_' in domain or any(
                native != '.' and (domain == native or domain.endswith('.' + native) or native.endswith('.' + domain))
                for native in self.PROTECTED_ZONES) or domain == '.':
            raise ManagementError('Native/root zones are protected')
        old = self.config.zones.get(domain)
        if self.action == 'create' and old or self.action in ('update', 'delete') and not old:
            raise ManagementError('Zone already exists' if old else 'Zone does not exist')
        if old and old['config'] != '/etc/named.conf':
            raise ManagementError('A zone in an external/vendor include is not owned by this workflow')
        if old and old['kind'] not in ('master', 'primary', 'slave', 'secondary', 'forward'):
            raise ManagementError('Only static primary, secondary and forward zones are supported')
        root_syntax = self.config.configs['/etc/named.conf']
        text = root_syntax.text
        new_zones = dict(self.config.zones)
        if self.action == 'delete':
            text = root_syntax.replace(old['clause'], '')
            new_zones.pop(domain)
            if self.request.get('purge_data', False):
                if old['kind'] not in ('master', 'primary') or not old['file'].startswith('/var/named/zones/'):
                    raise ManagementError('Only primary data in /var/named/zones can be explicitly purged')
                self.changes[old['file']] = None
        elif self.action == 'update':
            if old['kind'] != 'forward':
                if {'forwarders', 'dnssec_validate'} & set(self.request):
                    raise ManagementError('Forwarding fields cannot be applied to an authoritative zone')
                settings = self.request.get('settings')
                if not isinstance(settings, dict) or not settings:
                    raise ManagementError('Update a static zone using an explicit settings mapping; use record actions for its data')
                replacement = text[old['clause'].start:old['clause'].end]
                for name, value in settings.items():
                    if name in ('type', 'file', 'allow-update', 'update-policy', 'inline-signing', 'allow-transfer', 'masters', 'primaries', 'allow-notify', 'also-notify', 'transfer-source', 'notify-source'):
                        raise ManagementError('Zone storage/replication/security ownership cannot be changed in place')
                    replacement = self.block_setting(replacement, name, value)
                text = root_syntax.replace(old['clause'], replacement)
            else:
                if 'settings' in self.request:
                    raise ManagementError('Use forwarders and dnssec_validate for a forwarding zone')
                servers = self.forwarders(self.request.get('forwarders'))
                replacement = self.block_setting(text[old['clause'].start:old['clause'].end], 'forwarders', self.forward_value(servers))
                text = root_syntax.replace(old['clause'], replacement)
        else:
            kind = self.request.get('kind')
            if kind == 'forward':
                if {'serial', 'ttl'} & set(self.request):
                    raise ManagementError('A forwarding zone has no local SOA serial or TTL')
                servers = self.forwarders(self.request.get('forwarders'))
                declaration = 'zone "' + domain.rstrip('.') + '" IN {\n    type forward;\n    forward only;\n    forwarders ' + self.forward_value(servers) + ';\n};\n'
                new_zones[domain] = {'domain': domain, 'kind': 'forward'}
            elif kind == 'authoritative':
                if {'forwarders', 'dnssec_validate'} & set(self.request):
                    raise ManagementError('Forwarding fields cannot be applied to an authoritative zone')
                declaration, zone = self.authority(domain)
                new_zones[domain] = zone
            else:
                raise ManagementError('Create kind forward or authoritative')
            text += ('\n' if not text.endswith('\n') else '') + declaration
        # Manage only this suffix's NTA marker. No unrelated comments are removed.
        marker = '// Bind9-Ansible DNSSEC-PRIVATE ' + domain.rstrip('.')
        text = re.sub(r'(?m)^' + re.escape(marker) + r'[ \t]*\n', '', text)
        if self.action != 'delete' and new_zones[domain]['kind'] == 'forward':
            old_private = bool(re.search(r'(?m)^' + re.escape(marker) + r'[ \t]*$', root_syntax.text))
            private = not self.request.get('dnssec_validate', not old_private)
            if 'dnssec_validate' in self.request and not isinstance(self.request['dnssec_validate'], bool):
                raise ManagementError('dnssec_validate must be a YAML boolean')
            if private and self.dnssec_active(text):
                syntax = Syntax(text)
                target = next(c for c in syntax.clauses if c.name == 'zone' and canonical_name(c.header[1].value) == domain)
                text = text[:target.start] + marker + '\n' + text[target.start:]
        self.changes['/etc/named.conf'] = text.encode('utf-8')
        self.delegations(new_zones)

    @staticmethod
    def block_setting(text, name, value):
        if not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9-]*', name) or not isinstance(value, str):
            raise ManagementError('Use BIND option names and string values')
        statement = name + ' ' + value.strip() + ';'
        parsed = Syntax(statement)
        if len(parsed.clauses) != 1:
            raise ManagementError('One BIND setting per mapping entry')
        syntax = Syntax(text)
        block = syntax.clauses[0]
        old = Syntax.one(block.children(), name, False)
        if old:
            return syntax.replace(old, statement)
        offset = block.tokens[-2].start
        return text[:offset] + '    ' + statement + '\n' + text[offset:]

    def forwarders(self, value):
        if not isinstance(value, list) or not value:
            raise ManagementError('forwarders must be a nonempty list')
        servers = []
        for item in value:
            if not isinstance(item, dict) or set(item) - {'address', 'port'}:
                raise ManagementError('A forwarder accepts address and optional port only')
            if not isinstance(item.get('address'), str):
                raise ManagementError('A forwarder address must be an explicit IP string')
            try:
                address = ipaddress.ip_address(item.get('address', ''))
                port = item.get('port', 53)
                if address.is_multicast or address.is_unspecified or address.is_link_local or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                    raise ValueError()
                endpoint = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
                # Apply the same policy to IPv4-mapped IPv6 endpoints.
                if endpoint.is_multicast or endpoint.is_unspecified or endpoint.is_link_local or str(endpoint) == '255.255.255.255':
                    raise ValueError()
                peers = [self.context.get(key) for key in ('primary_front', 'secondary_front', 'primary_back', 'secondary_back')]
                if port == 53 and (endpoint.is_loopback or str(endpoint) in peers):
                    raise ValueError()
            except ValueError:
                raise ManagementError('Invalid forwarder address or port')
            servers.append({'address': str(address), 'port': port})
        return servers

    @staticmethod
    def forward_value(servers):
        return '{ ' + ' '.join(s['address'] + ' port ' + str(s['port']) + ';' for s in servers) + ' }'

    def authority(self, domain):
        context = self.context
        key = context.get('tsig_key_name', '')
        if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}', key) or key == 'rndc-key':
            raise ManagementError('Invalid site transfer key identity')
        keys = [c.header[1].value for syntax in self.config.configs.values() for c in syntax.clauses if c.name == 'key']
        if key not in keys:
            raise ManagementError('The site transfer key must already exist in the live configuration')
        for name in ('primary_front', 'secondary_front', 'primary_back', 'secondary_back'):
            try:
                address = ipaddress.IPv4Address(context.get(name, ''))
                if address.is_multicast or address.is_unspecified:
                    raise ValueError()
            except ValueError:
                raise ManagementError('Invalid peer identity for authoritative zone creation')
        name = domain.rstrip('.')
        if context.get('primary'):
            filename = '/var/named/zones/' + name + '.zone'
            if self.files.path(filename).exists():
                raise ManagementError('A pre-existing zone data file must not be overwritten by create')
            serial, ttl = self.request.get('serial', 1), self.request.get('ttl', 300)
            if any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 4294967295 for v in (serial, ttl)):
                raise ManagementError('Positive integer serial and ttl are required')
            data = ('; Bind9-Ansible-MANAGED-ZONE\n$TTL ' + str(ttl) + '\n@ IN SOA ns1.' + domain +
                    ' hostmaster.' + domain + ' ( ' + str(serial) + ' 3600 900 1209600 300 )\n' +
                    '@ IN NS ns1.' + domain + '\n@ IN NS ns2.' + domain + '\nns1 IN A ' + context['primary_front'] +
                    '\nns2 IN A ' + context['secondary_front'] + '\n')
            self.changes[filename] = data.encode('utf-8')
            body = ('type master;\n    file "' + filename + '";\n    allow-transfer { !{ !bind_secondary_back_ip; any; }; key "' + key +
                    '"; };\n    notify explicit;\n    also-notify { ' + context['secondary_back'] + ' key "' + key + '"; };')
            kind = 'master'
        else:
            filename = '/var/named/slaves/' + name + '.zone'
            # A retained old slave file can carry a higher serial and prevent
            # initial transfer of a freshly recreated primary zone.
            if os.path.lexists(str(self.root / filename.lstrip('/'))):
                raise ManagementError('A pre-existing secondary zone file must be inspected and archived before create')
            body = ('type slave;\n    file "slaves/' + name + '.zone";\n    masters { ' + context['primary_back'] +
                    ' key "' + key + '"; };\n    allow-notify { !{ !bind_primary_back_ip; any; }; key "' + key + '"; };')
            kind = 'slave'
        return ('zone "' + name + '" IN {\n    ' + body + '\n};\n',
                {'domain': domain, 'kind': kind, 'file': filename})

    def cuts(self, zones, syntax):
        auth = {name: z for name, z in zones.items() if z['kind'] in ('master', 'primary', 'slave', 'secondary')}
        cuts = {}
        for clause in syntax.clauses:
            if clause.name != 'zone':
                continue
            domain = canonical_name(clause.header[1].value)
            if domain not in zones or zones[domain]['kind'] != 'forward':
                continue
            ancestors = [a for a in auth if a == '.' or domain.endswith('.' + a)]
            if not ancestors:
                continue  # A secondary does not modify primary data files.
            parent = max(ancestors, key=len)
            if auth[parent]['kind'] not in ('master', 'primary'):
                continue
            block = Syntax.one(clause.children(), 'forwarders')
            servers = []
            for server in block.children():
                address = ipaddress.ip_address(server.header[0].value)
                servers.append(str(address))
            if any(domain != other and (domain.endswith('.' + other) or other.endswith('.' + domain)) for other, zone in zones.items() if zone['kind'] == 'forward'):
                raise ManagementError('Nested forwarding zones are not supported')
            if any(a != '.' and (a == domain or a.endswith('.' + domain)) for a in auth):
                raise ManagementError('A forward zone cannot mask an authoritative zone')
            names = ['bind-forwarder-' + str(i) + '.' + domain for i in range(1, len(servers) + 1)]
            cuts[(parent, domain, 'NS')] = names
            for owner, address in zip(names, servers):
                cuts[(parent, owner, 'A' if ipaddress.ip_address(address).version == 4 else 'AAAA')] = [address]
        return cuts

    def delegations(self, zones):
        old = self.cuts(self.config.zones, self.config.configs['/etc/named.conf'])
        new = self.cuts(zones, Syntax(self.changes['/etc/named.conf'].decode('utf-8')))
        for parent, domain, kind in new:
            if kind != 'NS':
                continue
            path = zones[parent]['file']
            data = self.changes.get(path, self.config.contents.get(path))
            document = ZoneDocument(data.decode('utf-8'), parent)
            for record in document.records:
                owner = record['name']
                if owner == domain or owner.endswith('.' + domain):
                    values = [' '.join(t.text for t in rr['tokens']) for rr in document.rrset(owner, record['type'])]
                    if values != old.get((parent, owner, record['type'])):
                        raise ManagementError('Manual authoritative data conflicts with the forwarding cut')
        for parent, owner, kind in sorted(set(old) | set(new)):
            before, after = old.get((parent, owner, kind)), new.get((parent, owner, kind))
            if before == after or parent not in zones:
                continue
            path = zones[parent]['file']
            data = self.changes.get(path, self.config.contents.get(path))
            document = ZoneDocument(data.decode('utf-8'), parent)
            existing = document.rrset(owner, kind)
            values = [' '.join(t.text for t in rr['tokens']) for rr in existing]
            if existing and values != before:
                raise ManagementError('A delegation/glue RRset was changed manually; refusing to overwrite it')
            if before and not existing:
                raise ManagementError('An expected delegation/glue RRset is missing')
            if after is None:
                changed = document.change('delete', owner, kind, None)
            else:
                changed = document.change('update' if existing else 'create', owner, kind, after)
            self.changes[path] = changed.encode('utf-8')

    def normalize_serials(self):
        """Advance each existing changed primary exactly once per transaction."""
        owners = {}
        for domain, zone in self.config.zones.items():
            if zone['kind'] in ('master', 'primary'):
                owners.setdefault(str(Path(zone['file'])), set()).add(domain)
        for path, data in self.changes.items():
            if len(owners.get(str(Path(path)), set())) > 1 and data != self.config.contents.get(path):
                raise ManagementError('Shared primary data requires an explicit migration before editing or purging')
        for domain, zone in self.config.zones.items():
            path = zone.get('file')
            if path not in self.changes or self.changes[path] is None or self.changes[path] == self.config.contents.get(path):
                continue
            original = ZoneDocument(self.config.contents[path].decode('utf-8'), domain)
            if any(record['type'] in ('RRSIG', 'DNSKEY', 'NSEC', 'NSEC3', 'NSEC3PARAM') for record in original.records):
                raise ManagementError('Signed primary data requires its signing workflow; static edits would invalidate signatures')
            before = original.serial()
            text = self.changes[path].decode('utf-8')
            after = ZoneDocument(text, domain).serial()
            target = int(before.text) + 1 if int(before.text) < 4294967295 else 1
            self.changes[path] = (text[:after.start] + str(target) + text[after.end:]).encode('utf-8')

    def validate(self):
        # Detect a missing runtime dependency before touching any live file.
        before = self.config.contents['/etc/named.conf']
        after = self.changes.get('/etc/named.conf', before)
        if self.dnssec_active(after.decode('utf-8')) and b'// Bind9-Ansible DNSSEC-PRIVATE ' in before + after:
            helper = self.root / 'usr/local/sbin/bind-private-dnssec'
            info = helper.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
                raise ManagementError('Missing or unsafe private DNSSEC helper')
        with tempfile.TemporaryDirectory(prefix='bind9-candidate-') as folder:
            stage = Path(folder)
            (stage / 'var/named').mkdir(parents=True)
            for name in ('zones', 'slaves', 'data', 'dynamic'):
                (stage / 'var/named' / name).mkdir()
            for name, data in dict(self.config.contents, **self.changes).items():
                if data is None:
                    continue
                target = stage / name.lstrip('/')
                target.parent.mkdir(parents=True, exist_ok=True)
                VerifiedIO.create(target, data)
            self.runner(['/usr/sbin/named-checkconf', '-z', '-t', str(stage), '/etc/named.conf'])

    def commit(self, revision):
        # Check bytes AND identities captured at observation, not fresh metadata
        # silently adopted after the candidate was approved.
        self.live_state = dict(self.config.contents)
        self.live_state.update({name: None for name in self.changes if name not in self.live_state})
        self.live_stamps = dict(self.config.observations)
        self.publications = {}
        self.assert_state('Configuration changed while the candidate was being validated')
        state = self.private_directory('/var/backups/Bind9-Ansible/management')
        backup = Path(tempfile.mkdtemp(prefix='transaction-', dir=str(state)))
        manifest = {'schema': 1, 'status': 'prepared', 'before': revision,
                    'action': self.action, 'resource': self.resource, 'files': {}}
        manifest.update(self.audit_details())
        for name, data in self.changes.items():
            old = self.config.contents.get(name)
            target = self.files.path(name)
            if old is None and os.path.lexists(str(target)):
                raise ManagementError('A new target appeared since planning')
            info = target.stat() if old is not None else None
            self.originals[name] = (old, info)
            if old is not None:
                saved = backup / name.lstrip('/')
                saved.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                VerifiedIO.create(saved, old)
                cursor = saved.parent
                while cursor != backup:
                    VerifiedIO.directory(cursor)
                    cursor = cursor.parent
            manifest['files'][name] = {'before': hashlib.sha256(old).hexdigest() if old is not None else None,
                                       'after': hashlib.sha256(data).hexdigest() if data is not None else None,
                                       'metadata': {'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)} if info else None}
        tool = self.root / 'usr/local/sbin/bind-configtool'
        info = tool.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ManagementError('The private deployed backup tool is missing or unsafe')
        backup_root = self.context.get('backup_root', '/var/backups/Bind9-Ansible/bind')
        if not isinstance(backup_root, str) or not backup_root.startswith('/') or '..' in Path(backup_root).parts or str(Path(backup_root)) != backup_root:
            raise ManagementError('The backup root must be a canonical absolute path')
        output = self.runner([str(tool), 'backup', '--force', '--root', backup_root]).strip()
        if not re.fullmatch(r'BACKUP_CREATED ' + re.escape(backup_root) + r'/[A-Za-z0-9_.-]+[.]tar[.]gz', output):
            raise ManagementError('No valid pre-management recovery archive was returned')
        manifest['full_archive'] = output.split(' ', 1)[1]
        archive = self.root / manifest['full_archive'].lstrip('/')
        # GNU sync with file operands fsyncs only these objects (no syncfs -f).
        # This also protects existing servers with the previous backup helper.
        self.runner(['/usr/bin/sync', '--', str(archive), str(archive) + '.sha256', str(archive.parent)])
        self.journal(backup, manifest)
        if Configuration(self.files).revision() != revision:
            raise ManagementError('Configuration changed while the candidate was being validated')
        self.assert_state('Configuration changed while the candidate was being validated')
        applied = []
        self.retain_lease = True
        try:
            # New data precedes declarations; purges follow declaration removal.
            for name in sorted(self.changes, key=lambda p: (self.changes[p] is None, p == '/etc/named.conf', p)):
                data = self.changes[name]
                old, info = self.originals[name]
                self.assert_state('A static asset changed immediately before commit')
                applied.append(name)
                # Track before publication: an fsync error can occur after rename.
                self.publication_guard = (self.live_state[name], self.live_stamps.get(name))
                self.publish(name, data, info, create=old is None)
                self.labels([name] if data is not None else [])
                self.assert_state('A static asset changed during publication')
            self.assert_state('A static asset changed before reload')
            self.refresh()
            self.health()
            self.assert_state('A static asset changed during reload/health checks')
            current = Configuration(self.files)
            if getattr(self, 'candidate_revision', None) and current.revision() != self.candidate_revision:
                raise ManagementError('The live static graph differs from the approved batch candidate')
            for name, expected in self.changes.items():
                actual = self.files.read(name) if self.files.path(name).exists() else None
                if actual != expected:
                    raise ManagementError('A published target changed during reload/health checks')
            final_revision = current.revision()
            manifest['after'] = final_revision
            manifest['status'] = 'committed'
            self.journal(backup, manifest)
            self.retain_lease = False
        except BaseException as error:
            failures = []
            for name in reversed(applied):
                try:
                    if name not in self.publications:
                        continue  # Never remove a foreign file after a failed create.
                    expected = self.changes[name]
                    old, info = self.originals[name]
                    target = self.files.path(name)
                    current = self.files.read(name) if target.exists() else None
                    if current == old:
                        continue  # Publication failed before replacing this file.
                    self.check_target(name, expected, self.live_stamps.get(name))
                    if expected is not None and self.files.observations[name][:2] != self.publications[name]:
                        raise ManagementError('Target replaced after publication; preserve it for operator recovery')
                    self.publication_guard = (expected, self.live_stamps.get(name))
                    self.publish(name, old, info, create=expected is None)
                    self.labels([name] if old is not None else [])
                except Exception as failure:
                    # Recovery must retain its journal/lease even for an
                    # unexpected native timeout or malformed external input.
                    failures.append(type(failure).__name__)
            if not failures and self.publications:
                try:
                    # Validate the actual restored graph: unrelated operator
                    # edits must never be replaced by an old complete backup.
                    restored = Configuration(self.files)
                    overlay = self.changes
                    self.changes = {}
                    try:
                        current_config = self.config
                        self.config = restored
                        self.validate()
                    finally:
                        self.config = current_config
                        self.changes = overlay
                    self.refresh()
                except Exception:
                    failures.append('reload')
            manifest['status'] = 'recovery-required' if failures else 'rolled-back'
            self.journal(backup, manifest)
            self.retain_lease = bool(failures)
            raise ManagementError('Management failed; ' + manifest['status'] + '. Protected recovery data: ' + str(backup)) from error
        return {'changed': True, 'checksum': final_revision,
                'action': self.action, 'resource': self.resource, 'paths': sorted(self.changes),
                'backup': str(backup), 'status': 'committed', 'serials': self.expected_serials}

    def check_lease(self):
        """Check ownership before mutations, not merely on context-manager exit."""
        owned = getattr(self, 'owned_lease', None)
        if owned is not None:
            try:
                current = (self.root / 'var/lib/Bind9-Ansible/.operation.lock').lstat()
            except OSError as error:
                self.retain_lease = True
                raise ManagementError('Host lease disappeared before publication') from error
            if (current.st_dev, current.st_ino) != owned:
                self.retain_lease = True
                raise ManagementError('Host lease ownership changed before publication')

    def check_target(self, name, expected, stamp):
        """Refuse inode, metadata or byte changes; existence checks include dangling links."""
        if expected is None:
            if os.path.lexists(str(self.files.path(name))):
                raise ManagementError('An absent target was created externally')
        elif self.files.read(name) != expected or (stamp is not None and self.files.observations[name] != stamp):
            raise ManagementError('A target was modified or replaced externally; preserve it for operator recovery')

    def assert_state(self, message):
        """Recheck every observed dependency, including those outside the edit set."""
        self.check_lease()
        try:
            for name, data in self.live_state.items():
                self.check_target(name, data, self.live_stamps.get(name))
            zones = list(self.config.zones.values())
            if hasattr(self, 'final_config'):
                zones += list(self.final_config.zones.values())
            if any(os.path.lexists(str(self.root / (zone['file'].lstrip('/') + '.jnl')))
                   for zone in zones if zone['kind'] in ('master', 'primary')):
                raise ManagementError('A zone journal appeared during static maintenance')
        except (OSError, ManagementError) as error:
            raise ManagementError(message) from error

    def private_directory(self, virtual):
        cursor = self.root
        for part in Path(virtual).parts[1:]:
            cursor = cursor / part
            try:
                cursor.mkdir(mode=0o700)
            except FileExistsError:
                pass
            info = cursor.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ManagementError('Unsafe management state directory')
        if stat.S_IMODE(cursor.stat().st_mode) != 0o700:
            raise ManagementError('Management state directory must be private mode 0700')
        return cursor

    def audit_details(self):
        """Additional private journal metadata, without request values."""
        return {}

    def publish(self, name, data, original, create=False):
        target = self.files.path(name)
        if data is None:
            self.check_lease()
            self.check_target(name, *self.publication_guard)
            target.unlink()
            self.publications[name] = None
            self.live_state[name] = None
            self.live_stamps.pop(name, None)
        else:
            descriptor, temporary = tempfile.mkstemp(prefix='.bind9-', dir=str(target.parent))
            try:
                with os.fdopen(descriptor, 'w+b') as stream:
                    os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode) if original else 0o640)
                    os.fchown(stream.fileno(), original.st_uid if original else 0,
                              original.st_gid if original else self.context.get('named_gid', 25))
                    VerifiedIO.write(stream, data)
                    staged = os.fstat(stream.fileno())
                # This second check follows temporary-file I/O. The earlier
                # preflight check alone leaves an avoidable lost-update window.
                self.check_lease()
                self.check_target(name, *self.publication_guard)
                if create:
                    os.link(temporary, str(target), follow_symlinks=False)
                else:
                    os.replace(temporary, str(target))
                self.publications[name] = (staged.st_dev, staged.st_ino)
                self.live_state[name] = data
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self.files.read(name)
            if self.files.observations[name][:2] != self.publications[name]:
                raise ManagementError('Published file replaced externally; preserve it for operator recovery')
            self.live_stamps[name] = self.files.observations[name]
        descriptor = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def labels(self, paths):
        if paths:
            self.runner(['/sbin/restorecon', '-F'] + [str(self.files.path(name)) for name in paths])
            for name in paths:
                before = self.live_stamps[name]
                self.files.read(name)
                after = self.files.observations[name]
                # SELinux relabeling can legitimately advance ctime only.
                if before[:-1] != after[:-1] or self.files.read(name) != self.live_state[name]:
                    raise ManagementError('A target changed during SELinux relabeling')
                self.live_stamps[name] = after

    def nta(self):
        config = self.files.read('/etc/named.conf').decode('utf-8')
        if not self.dnssec_active(config):
            return
        if '// Bind9-Ansible DNSSEC-PRIVATE ' in config or '// Bind9-Ansible DNSSEC-PRIVATE ' in self.config.contents['/etc/named.conf'].decode('utf-8'):
            helper = self.root / 'usr/local/sbin/bind-private-dnssec'
            info = helper.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
                raise ManagementError('The existing private DNSSEC helper is missing or unsafe')
            self.runner(['/usr/bin/python3', str(helper)])

    def refresh(self):
        """Reload files, reconcile private trust policy and expire only this cut.

        A cached SOA must not make an unavailable replacement forwarder appear
        healthy. Cached answers must also not outlive deletion of that zone.
        """
        self.runner(['/usr/sbin/rndc', '-s', '127.0.0.1', 'reload'])
        self.nta()
        if self.resource == 'zone':
            self.runner(['/usr/sbin/rndc', '-s', '127.0.0.1', 'flushtree', canonical_name(self.request['name'])])

    @staticmethod
    def dnssec_active(config):
        active = Syntax.one(Syntax.one(Syntax(config).clauses, 'options').children(), 'dnssec-validation', False)
        return active is None or active.scalar() != 'no'

    def health(self):
        """Check local UDP/TCP service and changed primary SOAs after reload."""
        expected = {}
        for name, zone in self.config.zones.items():
            if zone.get('file') in self.changes and self.changes[zone['file']] is not None:
                expected[name] = ZoneDocument(self.changes[zone['file']].decode('utf-8'), name).serial().text
        if self.resource == 'zone' and self.action == 'create' and self.request.get('kind') == 'authoritative' and self.context.get('primary'):
            name = canonical_name(self.request['name'])
            path = '/var/named/zones/' + name.rstrip('.') + '.zone'
            expected[name] = ZoneDocument(self.changes[path].decode('utf-8'), name).serial().text
        self.expected_serials = expected
        probes = [('localhost.', 'A', '127.0.0.1')] + [(name, 'SOA', serial) for name, serial in expected.items()]
        if self.resource == 'zone' and self.action != 'delete':
            domain = canonical_name(self.request['name'])
            kind = self.request.get('kind') or self.config.zones.get(domain, {}).get('kind')
            if kind in ('authoritative', 'master', 'primary', 'slave', 'secondary') and domain not in expected:
                probes.append((domain, 'SOA', '*'))
        if self.resource == 'zone' and self.action != 'delete' and (self.request.get('kind') == 'forward' or self.config.zones.get(canonical_name(self.request['name']), {}).get('kind') == 'forward'):
            probes.append((canonical_name(self.request['name']), 'SOA', None))
        self.check_probes(probes)

    def check_probes(self, probes):
        """Run bounded UDP/TCP probes shared by single and batch transactions."""
        for tcp in (False, True):
            for name, kind, serial in probes:
                attempts = 10 if serial == '*' and not self.context.get('primary') else 5
                for attempt in range(attempts):
                    output = self.runner(['/usr/bin/dig', '-r', '-4', '@127.0.0.1', '+tcp' if tcp else '+notcp',
                                          '+time=2', '+tries=1', '+ignore', '+recurse' if serial is None else '+norecurse', '+noall', '+comments', '+answer', name, kind])
                    valid = DnsReply(output).matches(name, kind, serial)
                    if valid:
                        break
                    if attempt == attempts - 1:
                        raise ManagementError('Post-reload DNS health or SOA serial did not converge')
                    time.sleep(2 if attempts == 10 else 1)

    @staticmethod
    def journal(folder, manifest):
        descriptor, temporary = tempfile.mkstemp(prefix='.manifest-', dir=str(folder))
        try:
            with os.fdopen(descriptor, 'w+b') as stream:
                VerifiedIO.write(stream, json.dumps(manifest, sort_keys=True, allow_nan=False).encode('utf-8'))
            os.replace(temporary, str(folder / 'manifest.json'))
            descriptor = os.open(str(folder), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            VerifiedIO.directory(folder.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextlib.contextmanager
    def lease(self):
        state = self.private_directory('/var/lib/Bind9-Ansible')
        lease = state / '.operation.lock'
        try:
            lease.mkdir(mode=0o700)
        except FileExistsError:
            raise ManagementError('Another operation or an interrupted transaction owns the host lease; inspect it before recovery')
        owned = lease.lstat()
        self.owned_lease = (owned.st_dev, owned.st_ino)
        self.retain_lease = False
        try:
            yield
        finally:
            if not self.retain_lease:
                try:
                    current = lease.lstat()
                except OSError as error:
                    raise ManagementError('Host lease disappeared; inspect operation ownership before recovery') from error
                if (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
                    self.retain_lease = True
                    raise ManagementError('Host lease ownership changed; preserve it for operator recovery')
                lease.rmdir()


class SnapshotFiles(SafeFiles):
    """Read only approved bytes while retaining real path/ancestor checks.

    Planning never falls back to a new live asset or publishes intermediate
    files. The initial complete graph remains the rollback and revision basis.
    """

    def __init__(self, files, contents):
        super(SnapshotFiles, self).__init__(files.root)
        if any(len(data) > self.LIMIT for data in contents.values() if data is not None):
            raise ManagementError('Candidate asset exceeds the supported size bound')
        if len(contents) > BoundedFiles.MAX_FILES or sum(len(data) for data in contents.values() if data is not None) > BoundedFiles.MAX_BYTES:
            raise ManagementError('Batch snapshot exceeds the 512 asset / 64 MiB aggregate bound')
        self.contents = dict(contents)

    def read(self, virtual):
        self.path(virtual)
        if virtual not in self.contents or self.contents[virtual] is None:
            raise ManagementError('Batch candidate references an unobserved or removed static asset')
        return self.contents[virtual]


class BatchManager(Manager):
    """One ordered, revision-and-plan-checked transaction across resource types.

    Existing single-resource planners build an overlay, never live mutations.
    The final candidate is validated once and uses the existing guarded commit,
    private backup and conditional rollback for all its changed files together.
    """

    MAX_OPERATIONS = 64
    MAX_REQUEST_BYTES = 256 * 1024

    def __init__(self, request, context, root='/', runner=None, check=False):
        if not isinstance(request, dict) or set(request) - {'resource', 'action', 'operations', 'expected_checksum', 'expected_plan_checksum'}:
            raise ManagementError('A batch accepts resource, action, operations and expected checksums only')
        if request.get('resource') != 'batch' or request.get('action') not in ('read', 'apply'):
            raise ManagementError('Use batch action read or apply')
        operations = request.get('operations')
        if not isinstance(operations, list) or not 1 <= len(operations) <= self.MAX_OPERATIONS:
            raise ManagementError('A batch requires 1 to 64 operations')
        try:
            encoded = json.dumps(operations, sort_keys=True, allow_nan=False).encode('utf-8')
        except (TypeError, ValueError):
            raise ManagementError('Batch operations must contain ordinary JSON-compatible values')
        if len(encoded) > self.MAX_REQUEST_BYTES:
            raise ManagementError('Batch operations exceed the 256 KiB request bound')
        # Own an immutable-from-the-caller copy and validate every schema early.
        self.operations = json.loads(encoded.decode('utf-8'))
        self.batch_digest = hashlib.sha256(encoded).hexdigest()
        identities, deleted, record_zones = set(), set(), set()
        for index, operation in enumerate(self.operations, 1):
            if not isinstance(operation, dict) or operation.get('action') not in ('create', 'update', 'delete') or 'expected_checksum' in operation:
                raise ManagementError('Batch operation ' + str(index) + ' must be a write without its own checksum')
            planner = Manager(operation, context, root=root, runner=runner, check=True)
            if planner.resource == 'record':
                domain = canonical_name(operation.get('zone', ''))
                owner, kind = planner.record_identity(domain)
                identity = ('record', domain, owner, kind)
                record_zones.add(domain)
            elif planner.resource == 'zone':
                domain = canonical_name(operation['name'])
                identity = ('zone', domain)
                if planner.action == 'delete':
                    deleted.add(domain)
            else:
                identity = ('option', operation['name'])
            if identity in identities:
                raise ManagementError('A batch cannot target the same resource more than once')
            identities.add(identity)
        if deleted & record_zones:
            raise ManagementError('A batch cannot delete a zone and operate on its records')
        self.request, self.context, self.root = dict(request), dict(context), Path(root)
        self.files, self.runner, self.check = BoundedFiles(root), runner or self.command, check
        self.resource, self.action = 'batch', request['action']
        self.changes, self.originals, self.results = {}, {}, []
        self.zone_operations = [op for op in self.operations if op['resource'] == 'zone']

    def selected(self, operation, config):
        """Read a selector against one snapshot, including not-yet-created zones."""
        fields = {key: value for key, value in operation.items() if key in ('resource', 'name', 'zone', 'type')}
        reader = Manager(dict(fields, action='read'), self.context, root=self.root, runner=self.runner)
        reader.config = config
        if operation['resource'] == 'record' and canonical_name(operation['zone']) not in config.zones:
            return {'found': False, 'zone_found': False}
        return reader.read()

    def plan(self):
        overlay = dict(self.config.contents)
        pending = []
        initial_serials = {}
        for index, operation in enumerate(self.operations, 1):
            result = {'index': index, 'resource': operation['resource'], 'action': operation['action'], 'name': operation['name']}
            if operation['resource'] == 'record' and not self.context.get('primary'):
                result.update(skipped=True, reason='primary-only', changed=False)
            else:
                planner = Manager(operation, self.context, root=self.root, runner=self.runner, check=True)
                planner.files = SnapshotFiles(self.files, overlay)
                planner.config = Configuration(planner.files)
                before = self.selected(operation, self.config)
                planner.plan()
                overlay.update(planner.changes)
                self.changes.update(planner.changes)
                result.update(skipped=False, changed=any(planner.config.contents.get(name) != data for name, data in planner.changes.items()))
                pending.append((result, operation, before))
                if operation['resource'] == 'zone' and operation['action'] == 'create' and operation.get('kind') == 'authoritative' and self.context.get('primary'):
                    path = '/var/named/zones/' + canonical_name(operation['name']).rstrip('.') + '.zone'
                    initial_serials[path] = (canonical_name(operation['name']), operation.get('serial', 1))
            self.results.append(result)
        self.normalize_serials()
        # New authorities start with the requested serial, even with many records.
        for path, (domain, serial) in initial_serials.items():
            text = self.changes[path].decode('utf-8')
            token = ZoneDocument(text, domain).serial()
            self.changes[path] = (text[:token.start] + str(serial) + text[token.end:]).encode('utf-8')
        self.changes = {name: data for name, data in self.changes.items() if self.config.contents.get(name) != data}
        self.final_config = Configuration(SnapshotFiles(self.files, dict(self.config.contents, **self.changes)))
        self.candidate_revision = self.final_config.revision()
        for result, operation, before in pending:
            result.update(before=before, after=self.selected(operation, self.final_config))

    def run(self):
        if os.geteuid() != 0:
            raise ManagementError('Management requires root')
        self.config = Configuration(self.files)
        revision = self.config.revision()
        if self.action == 'apply':
            expected = self.request.get('expected_checksum')
            if not isinstance(expected, str) or expected != revision:
                raise ManagementError('Checksum missing or changed; read the current state again before writing')
        self.plan()
        approval = {'before': revision, 'batch': self.batch_digest, 'candidate': self.candidate_revision, 'context': self.context}
        self.plan_checksum = hashlib.sha256(json.dumps(approval, sort_keys=True).encode('utf-8')).hexdigest()
        if self.action == 'apply' and self.request.get('expected_plan_checksum') != self.plan_checksum:
            raise ManagementError('Plan checksum missing or changed; preview this exact batch again before applying')
        if self.changes:
            self.validate()
        result = {'resource': 'batch', 'action': self.action, 'checksum': revision,
                  'plan_checksum': self.plan_checksum, 'batch_digest': self.batch_digest,
                  'operations_count': len(self.operations), 'paths': sorted(self.changes),
                  'operations': self.results, 'changed': False}
        if self.action == 'read':
            result['would_change'] = bool(self.changes)
        elif self.check:
            result.update(changed=bool(self.changes), check_mode=True)
        elif self.changes:
            result.update(self.commit(revision))
        return result

    def audit_details(self):
        return {'batch_digest': self.batch_digest, 'plan_checksum': self.plan_checksum,
                'operations_count': len(self.operations)}

    def refresh(self):
        """Reload once, reconcile private trust, and expire every affected cut."""
        self.runner(['/usr/sbin/rndc', '-s', '127.0.0.1', 'reload'])
        self.nta()
        for name in sorted({canonical_name(op['name']) for op in self.zone_operations}):
            self.runner(['/usr/sbin/rndc', '-s', '127.0.0.1', 'flushtree', name])

    def health(self):
        expected = {}
        probes = {('localhost.', 'A'): '127.0.0.1'}
        for name, zone in self.final_config.zones.items():
            path = zone.get('file')
            if zone['kind'] in ('primary', 'master') and path in self.changes:
                expected[name] = ZoneDocument(self.changes[path].decode('utf-8'), name).serial().text
                probes[(name, 'SOA')] = expected[name]
        for operation in self.zone_operations:
            name = canonical_name(operation['name'])
            if operation['action'] != 'delete' and (name, 'SOA') not in probes:
                probes[(name, 'SOA')] = None if self.final_config.zones[name]['kind'] == 'forward' else '*'
        self.expected_serials = expected
        self.check_probes([(name, kind, serial) for (name, kind), serial in sorted(probes.items())])
