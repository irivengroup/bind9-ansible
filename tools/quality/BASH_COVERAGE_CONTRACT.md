# Native Bash coverage contract

The QA controller runs two GNU Bash 5.2.37 interpreters built from the same
verified source archive, configuration and toolchain. One is unmodified; the
other has the reviewed observer patch. Production source bytes, shell words,
expansions, traps and external commands are unchanged. The observer never runs
on managed servers.

## Supported scope and units

The byte-pinned `roles/bind9/files/named-healthcheck.sh` profile measures all five
metrics. The reviewed static inventories contain no runtime hits. A separate
syntax-only native parse must reproduce every declaration, including code that
a particular runtime invocation never reaches.

- Statements: native command entries, excluding connector-only AST nodes.
- Branches: both outcomes of native control decisions and compound boolean
  decisions. A completed loop records exhaustion separately from body entry.
- Functions: actual entry into each authored function body; declaration alone
  provides no function execution credit.
- Lines: the union of authored statement-entry lines.
- Elementary conditions: each actually evaluated boolean operand and command
  status used as a predicate, with true and false counted independently. A
  short-circuited operand receives no inferred outcome. This is not MC/DC.

The health profile declares 71 statements, 46 branch outcomes, three functions,
39 executable lines and 44 elementary-condition outcomes.

The byte-pinned `roles/bind9/files/bind9-sbin-path.sh` is the single canonical
source copied to `/etc/profile.d/bind9-sbin-path.sh`. Its separate complete
profile declares three statements, six branch targets/outcomes, no authored
function, two statement-entry lines and six elementary-condition outcomes.
Case alternatives are native arm-entry targets. An unquoted literal `*` is
the unconditional default alternative; it declares no elementary boolean
operand, and a reported false match is refused. Other native pattern results
are elementary boolean outcomes. The complete parameter words are inventoried
by the native lexer with their authored lines; native expansion branches emit
their original selected choice without reevaluating the parameter or exposing
its value. The closed source has exactly one `${PATH:-…}` site and one
`${PATH:+…}` site. Both outcomes of each operator remain in both independent
denominators; an unexecuted assignment grants no expansion credit. This
specialized profile never infers choices for unreviewed parameter words.

The two additional source-pinned complete profiles measure **all five metrics**.
The exact current denominators for `bind-front-routes.sh` and
`bind-configtool.sh` come from `native_bash/bind-front-routes-profile.json` and
`native_bash/bind-configtool-profile.json`. Their source and observer-patch hashes
must match the reviewed source bytes and interpreter. A syntax-only native parse
must independently reproduce every declaration; each profile's `units` exposes
the separate statement, branch, function, line and elementary-condition sets.
These static declarations are denominators, not coverage scores. Counts from an
older source revision cannot stand in for the current inventory or its runtime
observations.

Static parsing covers the whole source, including every authored function,
nested function definitions, the reviewed literal trap bodies and code after an
early exit. Declarations alone provide no execution credit. A function-definition
statement does not imply entry into its function body.

Native lexical frames bind each command substitution to its actual retained
word and structural position. Provenance follows native word copies, unchanged
brace-expansion copies, quoted lexical slices and the exact assignment-RHS
call. Generated parameter strings and genuinely changed brace-expansion words
do not inherit a caller's origin. A complete final registry census must agree
with every nearest-word binding. Two identical substitutions in the same word
remain ambiguous and refuse admission. Runtime reparsing cannot create reviewed
source sites: only a unique existing word-owner/subtree pair can rebind. Each
node retains its authored position independently, because Bash can relocate
child lines differently from their enclosing control node. Unwind frames
restore observer context after non-local expansion errors.

Only the literal traps of the two exact reviewed script hashes are supported;
their complete native trees and actual signal bindings remain distinct from
execution. Arbitrary trap strings, unknown declarations, missing lexical
provenance and unattributed entries refuse admission. SIGINT's native
`interrupt trap` callback requires its actual SIGINT binding and the complete
reviewed tree. The superseded partial profiles and their duplicate inventory
files are removed. An independent fingerprint preserves the original command
and substitution-owner census. The larger complete inventories have a
separate 1 MiB ceiling; health and PATH keep their existing 128 KiB ceiling.

Function identities include the native structural path and complete tree
fingerprint. GNU Bash's `FunctionDef.line` can contain the last nested function's
parser line instead of the outer declaration's authored line. The observer uses
the declaration name's native lexical position, preserved through word copying
and cleared on allocation-cache reuse and disposal. It changes no interpreter
field or extdebug output. Missing lexical provenance refuses admission. Exact
function positions alone do not supply statement or line coverage: those
metrics require actual native command entries at their own authored positions.

Native arithmetic trees retain every node, child, root and authored purpose.
Logical operands and ternary selectors demand Boolean values; numeric operands
are not automatically conditions. Tested commands and arithmetic loop tests
consume the final native value. A same-PID completed root must accompany actual
node results; no skipped operand or missing result receives execution credit.
Legacy arithmetic root events are checked but never counted a second time.

The native word lexer inventories every parameter site and its parsed head.
The `+`, `-`, `=` and `?` selectors supply their original branch and elementary-
condition choice, paired separately for each PID. Every authored selector keeps
both outcomes in both denominators. Unparsed heads and unsupported runtime
grammar refuse admission. Source metadata has no runtime hit value. Parser
allocation serials can differ on a reparse; the line, lexical depth, lengths,
retained-text digest and structural site must all agree before comparing that
process-local serial with the saved inventory. Raw observations are retained.

Only the exact FRONT hash approves its reviewed single-word internal Boolean
command heads. Their values are closed to authored `true`/`false` assignments
and literal internal calls. The already computed native command status supplies
the predicate result; the observer never reads or reevaluates a variable value.
Other dynamic heads remain unsupported, including the same names in a different
source. This is a closed-source contract, not a generic Bash coverage claim.
Any new inline shell content remains unmeasured until its whole grammar is
supported, and required unknown metrics continue to block global acceptance.

## Provenance and bounded observations

An explicit private manifest identifies the verified archive, patch, recipe,
compiler tools, interpreter versions and both executable hashes. The build is
offline and refuses an existing output directory. The GNU license is retained
in `native_bash/COPYING`; build products and source caches stay outside the
project archive. Rebuilding after a recipe or patch change is mandatory.

`BIND9_NATIVE_BASH_MANIFEST` selects that private Linux toolchain. The paired
runner explicitly supplies phase, output and approved source identities; an
ambient manifest alone does not activate collection. Unreviewed shell or loader
initializers are refused. Only direct launches of the exact approved script
bytes are eligible. Child argument reporting and the uninstrumented reference
behavior remain intact.

The collector uses private Unix datagram sockets with dynamically allocated
close-on-exec descriptors and verifies kernel-supplied process credentials.

Declarative `Popen(user=...)` identities are resolved before launch and included
in the paired process signature with the exact arguments. Only root may assign
a different writer UID. The private writer socket is transferred to that UID;
the native hook still requires its owner to match the interpreter's effective
UID, and the reader still requires matching kernel PID/UID credentials. Child
hooks remain refused. QA interpreter files are root-owned, mode `0511`, within
a root-owned `0711` directory: other test users can execute the public GNU code
but cannot list the directory, read its private manifest, or modify any binary.
Source/build hashes, immutable metadata checks and event limits are unchanged.
Events contain source/structural identities, numeric kinds and outcomes, never
shell values, environment contents, command output or secrets. The channel
refuses unknown schemas, unsupported constructs, source changes, overflow,
spoofed process identity and incomplete parent receipts. Budgets are explicit:
eight simultaneous observations, 4,096 attempts, 100,000 event rows and 16 MiB
of aggregate receipt data; per-process channel limits also apply. Exceeding a
budget refuses admission rather than dropping observations.

## Complete CI campaign and native checker isolation

`run_coverage_campaign.py plan` collects the entire unfiltered `tests` tree once
and binds its exact identities to all production and QA source hashes. The
controller executes the main cohort; the `native-checker` Docker target executes
`test_management_acl_logging.py` with the real checker explicitly selected; the
`native-public` target supplies the deployed checker path for
`test_native_management_playbook.py`. Ordinary native filesystem fixtures keep
absent system command paths and retain their refusal to overwrite existing files.
The two extra images contain the pinned `bind9-utils`/`bind9-libs` checker only
for QA; they do not add a managed-server dependency.

The inspected shell-heavy modules run one test per paired batch. A real
four-case recovery group exhausted the existing observation-byte budget even
though every assertion passed. Isolating each case preserves the same complete
collection, source and limits; no refused partial facts receive coverage credit.

A cohort returns success only when every assigned test has a complete admitted
native/observed pair, without skips or errors. Its partial threshold refusal is
retained in its report. `run_coverage_campaign.py merge` independently re-admits
all sealed receipts through `CoverageGate`, requires the exact unfiltered union
with no duplicate identities, and enforces all five global and per-component
99.99% thresholds. Each admitted run also binds the actual JUnit class/name pairs
to its selected node IDs using the pinned pytest producer's name convention,
including classes, parameter IDs and XML character handling. Both phases agreeing on
renamed cases is insufficient. Campaigns require default JUnit names without
custom prefixes or attribute overrides; ambiguous exported identities are refused
before execution. Standalone paired tools retain generic selector support.
A missing/refused cohort, stale source, altered receipt or skipped
mandatory dependency blocks that final merge. No cohort success authorizes a
publication or deployment. The workflow retains failed and partial evidence.

## Admission and limitations

Both phases must execute the same test identities and source/QA revision. The
gate checks interpreter provenance, complete static declarations, actual facts,
process signatures and exit statuses before admitting any language's hits.
Both fresh pytest processes use the same private temporary pathname; pytest
recreates its contents before each phase. A regression control checks both
path equality and absence of native-phase files in the observed phase. Random
backup names are validated by the archive fixture, then assigned a deterministic
fixture name with a recalculated real checksum before restoration. Argument
comparison remains exact; ephemeral paths or tokens are never silently ignored.
The closed version-2 Bash artifact must include exactly the reviewed components
present in that project. Process, row and byte limits apply across all profiles;
removing or adding a profile, mixing modes or duplicating a PID is refused.
Unknown, forged or truncated Bash evidence prevents partial Python, template or
Ansible credit from that receipt. Zero script executions retain all declared
units with zero hits. Every applicable metric must independently reach 99.99%
before rounding, for each component and globally.

Protocol controls include malformed facts, toolchain changes, credential spoofing,
descriptor inheritance, absent executions and actual paired receipts corrupted
after execution. These establish the declared verifier contract; they do not
provide cryptographic attestation against a malicious QA administrator or
qualify production DNS, SELinux, systemd or network connectivity.
