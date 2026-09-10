# AI Systems Lab

Three runnable browser examples by Richard Russell / AI Venture X: controlled agent execution, programme impact analysis and transparent model evaluation.

## Run locally

Requires **Python 3.12+ and a browser**. No database installation, Node.js or third-party Python packages are required.

From the repository root:

```powershell
python examples/run.py
```

Open **http://127.0.0.1:8877**. Stop the server with **Ctrl+C**. The default session uses temporary workflow data; it is cleaned up on a normal shutdown. To retain it between sessions:

```powershell
python examples/run.py --data .example-data/my-lab
```

If the port is occupied, add `--port 8878` and use that port in the URL. You can also run the launcher by its absolute path from another directory.

| Example | What to try | Evidence |
| --- | --- | --- |
| [Agent Control Room](agent-control-room/README.md) | Propose, approve, revoke, revise and recover an action | Existing workflow engine, actual mock CRM records and persisted traces |
| [Programme Intelligence](programme-intelligence/README.md) | Delay equipment delivery and inspect affected workstreams | Dependency calculations, critical tasks, working-day dates and source-linked briefs |
| [Model Evaluation](model-evaluation/README.md) | Compare routing rules, live models or imported predictions | Fixed case labels, confusion matrices, macro F1, failed cases and measured usage |

All three work in **sample mode without credentials**. The Control Room always uses mock identities and a mock CRM. Programme calculations and local routing baselines execute real code; they are not prerecorded results.

## Optional live AI

Programme commentary and fresh model evaluations use the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/structured-outputs). Configure your own `OPENAI_API_KEY` in the process environment or an ignored repository `.env.local`, then start:

```powershell
python examples/run.py --allow-live --max-live-calls 8
```

The server reads the key; it never sends it to the browser. A model request happens only when you click **Generate AI commentary** or **Evaluate live model**. Each action makes one request and may incur provider charges. Supported models are GPT-4.1 mini and nano; access depends on your OpenAI project.

The call limit applies to this server session, includes failures and resets on restart. There are no automatic retries. Live results show the returned model identifier, request wall time and reported token usage. No monetary cost is inferred. No model is given an execution tool or permission to change the programme.

## Check it works

```powershell
python examples/smoke.py
python -m unittest discover -s examples/tests -v
```

Expected: **seven smoke-check groups pass** and **23 tests pass**. These checks make no model calls. They cover the three pages, approval and recovery, tenant separation, schedule calculation, prediction scoring, exports and live-mode gating. Browser interaction and actual provider behaviour require separate verification.

The original reference commands still work:

```powershell
python -m reference.smoke
python -m unittest discover -s tests -v
```

## Implementation map

- [Local server](server.py): loopback routing, static-file allowlist, session request checks and bounded JSON downloads.
- [Original workflow engine](../reference/workflow.py) and [HTTP adapter](../reference/server.py): reused by the Control Room without copying their permission logic.
- [Programme calculation](programme.py): validates dependencies, detects cycles and calculates early/late boundaries and total float.
- [Evaluation engine](evaluation.py): scores predictions against the versioned dataset and labels local, live and imported results.
- [Provider adapter](provider.py): optional structured responses, timeouts, response limits and a per-session call budget.
- [Tests](tests/test_examples.py): calculation, evaluation, provider and HTTP regression coverage.

The shared UI uses plain HTML, CSS and browser JavaScript. There is no frontend build step.

## Scope

These are local engineering examples on synthetic data. The server binds to loopback and is not a production hosting configuration. Public demo identities represent roles, not real-user authentication. It blocks cross-origin browser requests and serves an explicit asset allowlist, including no environment files.

Workflow state persists only when a data directory is selected. Programme scenarios and evaluation comparisons are browser-session state; reload starts a fresh view. Download JSON to retain a result. Download links are single-use, expire after five minutes and are held only in server memory, with at most ten pending files.

Programme dates exclude holidays, resource contention and probabilistic estimates. Source validation checks that AI citations exist; human review is still required to assess whether statements are supported. The small public routing dataset is a teaching example, not a general model benchmark or evidence of production safety.
