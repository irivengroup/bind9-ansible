# Native Ansible coverage contract

This QA observer is pinned to ansible-core 2.16.19 and Jinja 3.1.6. It is never
installed on managed servers. The receipt binds all source bytes, source
positions, executable units, QA code and individual worker artifacts.
The QA fingerprint includes the presence and bounded regular-file bytes of
the project's `ansible.cfg`. Changing, creating or removing it invalidates an
earlier receipt even when every test and production source byte is unchanged.
When present, the paired runner explicitly selects that project file in both
fresh child processes, including on mounted workspaces that appear writable.

The 2.16.19 native conditional wrapper assigns its result before inspecting its
original class. The observer unwraps its operand carrier before that assignment
and observes the actual subsequent truth conversion at the native decision.
It never substitutes a carrier class for the value inspected by Ansible. Native
generated list/type operations are not authored coverage units. Ternary guards
retain the selector and both possible value-arm conditions in their inventory;
an unselected arm receives no inferred outcome.

Receipts bind the exact controller version, Jinja version and native conditional
settings. The `_DISABLE_BACKPORTED_INSPECTIONS` escape hatch is refused. Boolean
settings must have JSON boolean types; integers cannot impersonate them. The
native `ALLOW_BROKEN_CONDITIONALS` setting is retained exactly, including its
refusals, and must match across parent and worker receipts.

## Units and observations

- **Statements:** one native action dispatch per authored task, plus authored
  Jinja output/assignment/control entries inside task fields and predicates.
  A skipped task does not count as an action dispatch. Predicate wrapper output
  inserted by Ansible is metadata, not an authored output statement.
  Explicit block entries are observed in committed native iterator states.
  Static imports count when their native loader is invoked (before runtime
  conditions on children); plays count at the native queue-manager entry and
  role invocations at their first committed native task. Meta actions count
  only when the native strategy reports that they were not skipped.
- **Branches:** native Jinja decisions and loops, plus each authored task loop's
  body-entry and iterator-exhaustion edges. A completed nonempty loop exercises
  both edges; an empty loop exercises exhaustion only. An exceptional exit does
  not imply iterator exhaustion. Native guard wrappers expose the outcomes of
  `when`, `until`, assertions, `changed_when` and `failed_when`.
  A block with `rescue` has a recovery-entry edge and a normal-exit edge.
  Scheduling peeks stage events; only the exact state subsequently committed
  for the same host admits those events. Uncommitted peeks contribute nothing.
- **Functions:** no authored function construct exists in this YAML profile.
  Native modules and action plugins are calls, not functions defined in YAML.
  Jinja macros/scoped callable constructs are refused by the parser. Thus this
  metric has denominator zero and is NON APPLICABLE, never 100 percent.
- **Lines:** the union of authored statement entry lines, including the original
  YAML start of each dispatched logical task and source lines of Jinja outputs.
  Literal mapping entries and native Python implementation lines are not YAML
  executable lines.
- **Elementary conditions:** true/false outcomes requested by native evaluation
  of each nonconstant Jinja boolean operand. Short-circuited operands receive no
  inferred outcome. This is condition coverage, not MC/DC.

## Native module and service boundaries

The isolated file suites execute canonical stat/slurp/file/copy/template tasks
through Ansible's genuine modules. Play-relative asset links preserve canonical
source bytes and original positions. Canonical handlers execute through the
native notification queue, including unit tests with an explicit synthetic
caller notification; those callers do not count as production action dispatch.

Service suites retain Ansible's genuine systemd module and replace only the
external systemctl command with a guarded facade over the existing unit model.
This measures YAML orchestration, ordering, propagation of command failures and
actual file output; it does not qualify a real systemd daemon, boot, SELinux or
DNS transport. Those require their separate native integration controls.
Protected no_log contents stay censored in callbacks; their contracts are
asserted inside the native play instead of bypassing that protection.
Management integration resolves the canonical module and utility through the
native Ansible loaders and dependency packager. It uses the real named-group
lookup, protected filesystem graph, operation lock and censored module result;
neither privileged paths nor source positions are relocated. Read operations,
unchanged batch approvals and pre-publication refusals preserve the input bytes.
The secondary DNS command has an exact argv/reply boundary and bounded retry
sequences. This qualifies orchestration, not actual DNS replication or commits,
which require the separate native daemon integration suite.
The unchanged public management playbook also runs through its native queue
manager, dynamic role loader and setup workers with two ordered local aliases.
They deliberately share one disposable filesystem for read/unchanged-plan
scenarios. Tests exercise both check-mode paths and verify that preflight refusal
on either alias prevents the application play. They do not model two independent
servers. Role search configuration is scoped to the canonical role and restored.
Directory integration also introduces a real symbolic link after successful
native path preflight. The writing action must refuse the link itself and
preserve the foreign directory's owner, mode, contents and children. This checks
the operation's no-follow setting rather than treating earlier stat success as
an immutable filesystem guarantee.
The recovery fixture triggers a synthetic failure outside the measured source,
then executes the untouched rescue mappings through Play.load and the native
task queue. Every mapping retains its original DataLoader source position.
The wrapper cannot cover the unvisited production transaction or invent its
recovery outcomes. Filesystem restoration and independent backup continuation
are observed separately; the backup command boundary is explicitly doubled.
The template observer preserves both inspected Jinja code-generator families.
The six literal template consumers are independently checked against all role
template files. Their plain Jinja inventory remains intact. The exact inspected
Ansible 2.16.19 environment classes and lexer settings also have a predeclared
inventory, with separate IDs whenever trimming changes authored units. Each
distinct profile must independently meet every applicable 99.99% threshold; an
aggregate cannot mask an insufficient native profile. Unknown environments,
changed lexer settings and unknown compilers remain refusals. Refusals are recorded and
propagated as syntax-family exceptions, because Ansible otherwise returns its
unrendered input after generic compilation exceptions.
Ansible's environment copies may alternate between YAML expressions and file
templates. Both observers share an exact compiler ownership registry: only a
registered generator and its exact bound owner probe can switch families.
Known closed-owner probes are inert; their copied environments may be reclaimed
while the compiler's weak provenance registration remains alive.
Unknown subclasses, foreign probes and changed profiles remain refusals.

## Incremental admission without shrinking the project

All 38 YAML files remain inventoried. Literal defaults and empty role metadata
have no executable units. A task file can receive complete metrics only when its
entire grammar uses observed tasks, fields, predicates and task loops, and at
least one source-bound native action dispatch or authored control entry proves
its executor was observed.
Unvisited units in that file remain in the denominator. Predicate-only tests do
not satisfy this requirement and never masquerade as task execution.

Verified guard/field facts are retained across admitted batches separately
from the native execution witness. They remain unpublished as complete file
metrics until an admitted native dispatch/control receipt activates that file.
The union is independent of batch size or admission order. A skipped task
contributes only the guard actually evaluated, never a dispatch. Unknown inline
bodies stay unmeasured. Invalid receipts commit neither pending facts nor
execution witnesses, including failures discovered in another language's proof.

Copied executable script bodies and inline shell bodies remain NON EXÉCUTÉ
until their own language observer is implemented and verified. Dispatching a
copy/shell action cannot cover the embedded executable statements. Other files
can have insufficient measured metrics. Neither case
is an exclusion, a zero denominator, or an accepted project. The project gate
still requires all five applicable metrics at least 99.99 percent before rounding,
globally and per component, with all mandatory controls successful.

Workers save IDs and boolean outcomes before Ansible's process exit. The parent
validates every receipt before union, preserves its SHA-256 and verifies the
merged counters against those workers. Unknown units, nonboolean outcomes,
changed sources, unsupported grammars and incomplete required surfaces fail
closed. Native and instrumented runs must execute the same test identities.
Template observations are nested inside the same worker envelope and share its
unchanged 16 MiB individual, 512 MiB aggregate and 4096-worker limits. A worker
may not omit them when the project has file templates. The gate independently
rebuilds the union from the hashed envelopes and uses one common source/unit
validator for parent and worker template facts. Parent-only rendering cannot
stand in for template execution that occurs in an Ansible worker.

## Complete collection in bounded paired batches

`run_coverage_suite.py` collects the requested pytest identities once and runs
every identity in exactly one sequential batch. Explicit deselection, ignored
paths, collection failures, skipped modules and omitted/duplicated test counts
refuse the suite. Each batch retains the unchanged native/instrumented equality,
source hashes, worker identity/digest checks and 16 MiB / 512 MiB / 4096-worker
limits. Batching never changes a source-unit denominator or the 99.99% thresholds.
Exact batch identities travel through bounded JSON manifests and pytest 9.0.3's
native argument-file parser. This avoids operating-system argv limits for long
parameter identities without increasing the existing 64 KiB command budget or
the 16 MiB manifest budget. Duplicate identities, line separators, argument-file
indirection, option injection, unsafe files and unknown schemas are refused.

A valid partial batch may miss thresholds; its source-bound observations can
be combined by the existing gate. A failed or mismatched batch contributes no
accepted receipt and keeps the final result refused. All refusals and artifacts
remain available. The final project result is calculated by the same gate over
every admitted batch; a completed test collection alone cannot qualify it.

Native Python collection supplies the role root and every inventoried Python
parent directory to coverage.py. This preserves unimported modules in namespace
directories, with their complete missing lines and branches and zero execution
hits. No artificial import, exclusion, shortened denominator or inferred hit is
used to make a partial selection admissible.

## Pinned legacy parser dependency

Component compatibility tests share the unmodified EL8 `ipaddress` implementation
in `tests/fixtures/legacy_ipaddress_el8.py` (SHA-256
`97d6fbeef7261d5bdc336228d4482749bdec1761360e7684ff5c50d31406a91a`). It comes
from `python3-libs-3.6.8-56.el8_9.rocky.0.x86_64` in the already tested EL8 image
`sha256:f0a5d0de5fc1f22a648f578640f34572675c93a81703650a2c0eb2cd12b32a4f`.
Its original copyright notice and package license are retained; the latter is
`tests/fixtures/PYTHON36_LICENSE.txt` (SHA-256
`b9a6d9320b8f2693e8d41e496ce56caadacaddcca9be2a64a61749278f425cf2`).

This QA-only fixture adds no deployed dependency. Its bytes are checked before
executing the same buffer in a new module for each test; no altered parser result
or invented coverage event supplies credit. It exercises real legacy behavior
that differs from the current stdlib: zero-padded IPv4 spelling and classification
of IPv4-mapped IPv6 endpoints. Native EL8 probes independently verify these
differences. The fixture is included in the existing compile/lint checks and QA
source hashes; existing receipt checks and production inventories are unchanged.
These component tests are not a claim of five-metric execution on native EL8.
