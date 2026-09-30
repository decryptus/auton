# Engineering requirements

These are maintainer requirements. Existing violations are debt, not examples.
See [the architecture review](docs/architecture-review-2026-09-27.md).

- Business/application behavior must be callable independently of CLI, ncurses,
  web presentation and HTTP request objects. Interfaces translate input, call
  shared services and present results; they are not application engines.
- Never implement an API operation by importing/calling a CLI entry point,
  constructing artificial command arguments or delegating to UI-owned callbacks.
- Put HTTP decoding, headers, status mapping and serialization in transport
  adapters; terminal interaction and exit codes belong to command interfaces.
  Pass explicit data and caller identity to services, with domain results/errors.
- Keep authorization and operation validation server-side. Interface filtering
  and disabled controls do not grant permission or replace service validation.
- Use explicit adapters/composition for storage, execution, clocks and lifecycle.
  Avoid new process-global mutable application state and hidden import side effects.
- Declare fixed routes, field sets, schemas, registries, limits and regex patterns
  as named uppercase module constants. Dynamic per-request objects remain local.
- Centralize identifier validation and use full matching. Preserve this project's
  naming and selector contracts; do not copy Galliflow's grammar here.
- Bound externally supplied regex work where supported, preserve matching semantics,
  and reject invalid selectors explicitly without broadening their selection.
- Reuse DWho, HTTPdis and Sonicprobe according to their roles. A transport library
  may expose HTTP objects; neutral services must not require those objects.
- Inspect lazy imports, callbacks and initialization paths during reviews. For a
  refactor, add behavioral tests with interface imports blocked and fake adapters;
  green functional tests alone do not demonstrate architectural separation.
- Avoid unnecessary production log/disk writes, retaining promised durability.
  Do not claim documentation alone is an automated architecture gate.

## Auton boundaries

- Job admission, ownership, capacity, expiry, status and output retention belong
  to a job service, independent of JobModule and HttpReqErrJson.
- Workers receive job inputs and an authenticated principal, never a retained HTTP
  request. Keep endpoint authorization and ownership enforcement in the service.
- Executing configured target commands is Auton's intended function. Keep that
  execution in the plugin/adapter with existing deadlines, bounded output and
  process-group cleanup. Do not confuse this with launching Auton's CLI to serve HTTP.
- Extract a reusable remote client from bin/auton; terminal rendering and argument
  parsing stay in the executable. Preserve safe retry and output-offset semantics.

## Test discovery and execution

- Verify the actual runner and every discovery root before adding or changing
  tests. A green command does not prove that all test declarations were loaded.
  Do not put standalone pytest functions into a suite run only by unittest.
- Run `.github/scripts/check-test-collection.py` with Python, the same
  interpreter/environment as the tests, and the declared runner. Pass separately
  discovered directories together when they are nested. The guard compares
  declarations in `test*.py` files with actual collection; it does not run tests.
- Reject empty suites, import/collection errors, duplicate definitions and
  declarations omitted by the runner. Keep test helpers out of the `test*`
  namespace. Explain intentional skips and separate integration prerequisites;
  never hide failures with `continue-on-error` or `|| true`.
- Run the normal test command after the guard. Report collected/executed/skipped
  counts and investigate unexpected changes; collection alone is not a passing
  test run. Parameterization may produce several cases per declaration.
- Check test paths, naming patterns, selection filters and CI commands together.
  A new test directory or non-Python test harness needs an explicit CI entry;
  this guard only covers the directories and Python naming pattern passed to it.
- Verify installed-package tests outside the source checkout where applicable.
  Architecture scans must resolve the package under test and reject empty scans.
- Preserve supported interpreter matrices. Changing runners requires an explicit
  decision and collection parity; this Python 3.8+ CI helper does not replace
  legacy-interpreter execution or integration/system acceptance tests.

Current project runner: `unittest` for `tests`. CI helper tests use
`unittest` in `.github/tests`.

## Interface feedback and visual acceptance

- Apply this convention to every project and delivery surface (Web, CLI/TUI,
  documentation, screenshots and videos): blue/informational for accepted or
  queued requests, a distinct labeled in-progress state, green for confirmed
  success, amber for warnings/uncertain outcomes, and red for failures/refusals.
- Submission/HTTP acceptance is not execution success. Never label it completed
  or reuse an error banner for a normal confirmation. Keep explicit text/icons;
  color alone must not carry meaning. Preserve readable contrast.
- Review normal, in-progress, success, warning and failure states before delivery,
  including mobile. Check wording, severity and computed visual styles together.
  Exercise the affected real interface flow when changing status presentation.
- Review generated screenshots/videos for semantic mistakes as well as layout.
  Generate them from the pinned, accurately labeled source; do not retouch images
  to hide a product defect. Regenerate affected media after the source correction.
- Add a focused regression check for a discovered severity mismatch. A passing
  build/link check or screenshots alone do not prove correct user feedback.

## Shared documentation screenshots

- GitHub README screenshots use https://auton.run/manual-captures/ and the same
  generated assets as the website manual. Do not commit frozen PNG copies or
  replace documentation scenarios with unrelated marketing recordings.
- Keep capture scripts runnable from a clean checkout. The website regenerates
  all captures from its pinned source on every deployment and scheduled build.
  Its manifest records source revision, version and SHA-256 checksums.
- Review generated artifacts for scenario/legend agreement. A green browser test
  alone does not update the images displayed by GitHub; verify the shared URLs.
