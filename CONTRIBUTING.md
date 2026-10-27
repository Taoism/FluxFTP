# Contributing

Use a focused branch and describe the problem, resulting behavior and validation
in each pull request. Keep network and file operations cancellable, preserve
existing destinations on failure, and test destructive actions with temporary
local fixtures. Follow the surrounding Python/Qt style.

See [BUILDING.md](docs/BUILDING.md) for setup and checks. Add a regression test for
behavioral fixes. For interface changes, include a screenshot with synthetic paths
and accounts. Note which protocols/platforms were actually tested.

Bug reports should include FluxFTP version, operating system, protocol, expected
behavior, observed behavior and a minimal reproduction. Redact hostnames and paths
if private. Never include account tokens, passwords, private keys or raw credentials.
Do not use a production server to demonstrate destructive failures.

Contributions to FluxFTP's original code are provided under the repository's MIT
license. Keep third-party notices with copied or adapted third-party material.
