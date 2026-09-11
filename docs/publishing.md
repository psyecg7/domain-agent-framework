# Package publishing

The repository releases every distributable package at the same version. Before
using the publishing workflow, build and validate a tagged release through the
normal release process; TestPyPI and PyPI files are immutable after upload.

## TestPyPI trusted publishing

`.github/workflows/publish-testpypi.yml` is deliberately manual. It builds
each distribution in an unprivileged job, transfers only the resulting wheels
and source archives to a separate publishing job, then asks TestPyPI to accept
GitHub's short-lived OIDC identity. It has no stored package-index token.

Do this once for every project below in a TestPyPI account you control:

```text
domain-agent-core
domain-agent-app
domain-agent-conformance
agent-delta
agent-enterprise
agent-lancedb
agent-ollama
agent-openai
agent-postgres
agent-redpanda
agent-scheduler
```

For each project, configure a normal or pending GitHub Trusted Publisher with:

```text
owner:       psyecg7
repository:  domain-agent-framework
workflow:    publish-testpypi.yml
environment: testpypi
```

A pending publisher does not reserve a name, so configure it immediately
before the first upload. In GitHub, create the `testpypi` environment under
**Settings → Environments** and add required reviewers if a second maintainer
is available. The environment name must exactly match the workflow.

Then choose **Actions → Publish to TestPyPI → Run workflow**. Do not rerun a
version that TestPyPI already contains: bump all package versions and create a
new release candidate instead.

After publication, test a clean install. TestPyPI often lacks third-party
dependencies, so retain PyPI as an extra index:

```bash
python -m venv /tmp/domain-agent-testpypi
source /tmp/domain-agent-testpypi/bin/activate
python -m pip install --upgrade pip
python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  domain-agent-app==0.2.0
python -c 'import agent_app, agent_core; print(agent_app.__name__)'
```

Use the release candidate version in place of `0.2.0` when validating a later
release. Also install representative adapter combinations—such as
`agent-postgres` and `agent-redpanda`—because these validate cross-package
version constraints.

## Promoting to PyPI

TestPyPI is not an automatic promotion path. After a TestPyPI release passes,
configure separate PyPI trusted publishers using a dedicated production
workflow and a protected `pypi` GitHub environment. Keep the TestPyPI and PyPI
workflows separate so a test upload can never publish a production package.

The workflow follows PyPI's trusted-publishing guidance: the publishing job
alone receives `id-token: write`, and the official PyPA action exchanges that
identity for a short-lived upload credential. It also publishes provenance
attestations by default. See the [PyPI trusted publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
and [pending publisher setup](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).
