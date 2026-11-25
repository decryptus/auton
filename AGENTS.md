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

## Separate user and contributor documentation

- Maintain two distinct entry points and tables of contents: user documentation
  for installation, configuration, operation, public APIs and troubleshooting;
  contributor documentation for architecture, internals, tests, benchmarks,
  release engineering and development plans.
- Keep README and package descriptions focused on users. Link to the contributor
  guide instead of embedding maintainer procedures. Library API examples belong
  in the user guide when they are needed to integrate the library.
- Keep registry publishing, CI setup, repository secrets and maintainer account
  configuration out of user guides, website manuals and package descriptions.
  Never include credential values or private infrastructure evidence in either guide.
- Put implementation reviews and acceptance records under the contributor
  navigation. Preserve user-facing compatibility limits, migration instructions,
  security requirements and failure semantics in the user documentation.
- Apply this separation to generated documentation and FR/EN website content.
  Update source content and generators together; do not patch only generated HTML.
- Before delivery, inspect both entry points, check links and build documentation
  with warnings treated as errors where supported. Review README/package text and
  the deployed manual for accidental maintainer content. Preserve private-project
  visibility and existing review/publication approval requirements.

## Optional Textual presentation

- Reuse `dwho.tui.textual` for shared layout, widgets and visual states. Keep
  collection, permissions, rules and execution in application/client services.
- Maintain the separate `textual_tests` unittest discovery root and optional CI
  job. Base commands must work without Textual/Rich imports.
- Generate candidate captures with `scripts/capture_textual.py`; keep synthetic
  fixtures labelled and record source hashes. Publish shared website captures
  only after the corresponding interface and source revision are accepted.

## Complete delivery and release synchronization

- Treat every delivery as a consistency check across the roadmap, PyPI packages,
  Docker Hub images and registry descriptions, repository/user/contributor
  documentation, demos, and existing product websites. Update every affected
  surface; a merged PR alone does not establish a completed delivery.
- Reconcile roadmap items with actual implementation and verified delivery.
  Distinguish planned, implemented, validated and published/deployed work; keep
  remaining limits and next steps explicit. Do not leave completed releases
  listed as pending or mark unverified work complete.
- For a release, publish the validated packages and images through the existing
  release gates, verify their versions/artifacts in the registries, and align
  installation commands, Compose examples, release notes and supported versions.
  Never overwrite an immutable published version to update its contents.
- Update affected documentation in every maintained language, including README,
  guides, examples, screenshots and diagrams. Keep user and contributor material
  separate and regenerate derived assets from the accepted source.
- Update existing demos and websites when affected, including source/image pins,
  visible versions, shared captures and release metadata. Validate the deployed
  result, links and relevant desktop/mobile flows; preserve private/public
  boundaries, credentials, data and the existing approval scope.
- Inspect applicability on every change. A documentation-only change does not
  require an artificial package/image release; a project without a registry,
  demo or website does not require inventing one. Record why a surface is
  unchanged, not applicable, blocked or explicitly deferred.
- Complete the authorized delivery work before reporting success. In the final
  handoff, distinguish merged code from registry publication and deployment,
  report checks performed, and list any required surface still not updated with
  its concrete blocker. Never imply that a planned publication already happened.

## Coding style and validation

- Prefix private Python module constants with an underscore (`_UPPER_CASE`).
- Centralize each identifier grammar, compile fixed regexes once and validate the entire identifier. Preserve existing selector semantics.
- Write technical error messages in English and expose stable machine-readable error codes. Keep localized user-facing text in the presentation layer.

## Shared component placement

- Before adding a dependency, implementing a local component or preparing a
  release, inspect the current capabilities of DWho, HTTPdis and Sonicprobe and
  reuse applicable functionality.
- For a new generic capability, assess whether it belongs in one of these shared
  libraries and record the placement decision in the contribution. Keep
  product-specific business rules in this repository.
- When a shared-library change is needed, validate it in its own repository
  against its contracts, supported versions and existing consumers. Then update
  this project's dependency and verify the integration before delivery. Follow
  each repository's release procedures and existing authorization scope.

## Deliverable and deployment contents

- Every deliverable and deployment ships only what the product needs to install,
  run and meet its legal obligations. This covers source tarballs and sdists,
  wheels, Docker images, Debian packages, GitHub release assets, browser
  extensions, mobile packages (APK/AAB/IPA), Claude skills and plugins, ChatGPT
  apps and instructions, and published websites.
- Never ship repository or maintainer material: `AGENTS.md`, `CLAUDE.md` and
  other agent instructions, roadmaps, contribution guides, reviews and acceptance
  records, CI configuration (`.github/`), tests and fixtures, development
  scripts, Dockerfiles and build tooling, editor/VCS files, local configuration,
  secrets or credentials.
- Keep what is required: runtime code and assets, the default configuration and
  examples the product loads or documents for installation, `LICENSE` and
  required notices, and the README or metadata a registry needs to describe the
  package. Ship user documentation only in deliverables meant to carry it.
- Select contents with explicit allow-lists: a `.dockerignore` starting with `*`,
  a `MANIFEST.in` without broad `graft`, explicit package data, a dedicated build
  directory for sites and plugins, and `export-ignore` in `.gitattributes` for
  GitHub source archives. Never publish a repository root or `COPY . .` into a
  final image.
- Before each release or deployment, list the actual artifact contents (sdist and
  wheel listings, image filesystem, archive listing, deployed URLs) and remove
  any non-essential file before publishing. Report this check in the handoff.

## Configuration file format

- Prefer YAML (`.yaml` / `.yml`) over JSON for project configuration files: it is
  easier to read, supports comments and produces clearer diffs.
- JSON remains acceptable when a tool or ecosystem requires it or does not support
  YAML (for example `package.json`, `tsconfig.json`, `composer.json`). Converting
  existing JSON files is not required.
- When a tool accepts both formats, use YAML for new configuration.
- In YAML, quote ambiguous scalars (`"yes"`, `"no"`, `"on"`, `"off"`, versions such
  as `"1.10"`) to avoid implicit type conversion.

## Commit authorship

Write every commit as `decryptus <decryptus@users.noreply.github.com>`, as both author and committer (run `git config user.name decryptus` and `git config user.email decryptus@users.noreply.github.com` before committing, including when rebasing). Do not add `Co-Authored-By` lines, session links or any mention of an assistant or tool in commit messages. Do not rewrite history already published on the default branch to fix older commits.
