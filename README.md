# Enterprise Agentic AI

A runnable Python reference for controlling what an agent may do: approve a specific action, enforce its limits, and recover from a lost response without duplicating CRM records.

**Public scope:** working reference code and architecture documentation. Agent behaviour and the CRM are simulated; commercial implementations remain proprietary.

[![Reference checks](https://github.com/Poochaman/enterprise-agentic-ai/actions/workflows/reference.yml/badge.svg)](https://github.com/Poochaman/enterprise-agentic-ai/actions/workflows/reference.yml)

## Run the checks

Requires Git and Python 3.12 (the tested version). No API keys or third-party Python packages are needed.

```powershell
git clone https://github.com/Poochaman/enterprise-agentic-ai.git
cd enterprise-agentic-ai
python -m reference.smoke
```

Run Python commands from the repository root. On systems that name Python 3 `python3`, substitute that command.

Expected result: **seven HTTP smoke checks pass**. They cover approved execution, replay, approval expiry and revocation, revised proposals, batch limits, tenant isolation, and the final mock CRM records.

For the narrated recovery example, run these commands in the same PowerShell session:

```powershell
$demoData = '.reference-data/demo-' + [guid]::NewGuid().ToString('N')
python -m reference.demo --data $demoData
python -m reference.demo --data $demoData
```

The first run simulates a CRM write succeeding while its response is lost, then recovers the existing result. The second returns the completed result without creating another lead. The [runbook](reference/README.md) explains the output and provides other run options.

## Interactive examples

Run `python examples/run.py` from this folder and open **http://127.0.0.1:8877**.

| Browser example | What to explore |
| --- | --- |
| [Agent Control Room](examples/agent-control-room/README.md) | Proposals, approvals, action limits, revocation and response-loss recovery through the existing engine |
| [Programme Intelligence](examples/programme-intelligence/README.md) | A supplier-delay scenario, calculated schedule impact and optional source-linked AI commentary |
| [Model Evaluation](examples/model-evaluation/README.md) | Local routing baselines, optional live models, failed cases and downloadable results |

All three have runnable no-key modes. Live AI is opt-in and requires your own provider credentials. [Setup, scope and tests](examples/README.md).

## What the reference demonstrates

| Engineering concern | Observable behaviour |
| --- | --- |
| Separation of proposal and approval | A requester proposes an action; a separate approver authorises its exact digest. |
| Bounded authority | Approvals expire, can be revoked, and limit the number of CRM records. |
| Changed instructions | Revising a proposal invalidates its approval. |
| Tenant boundaries | Identity determines tenant access; client overrides are rejected. |
| Recovery after response loss | The workflow reconciles the committed mock CRM result before considering another write. |
| Reviewable execution | Persisted state and event traces show routing, approval and execution decisions. |

These checks demonstrate behaviour in the local reference. They do not measure model quality, production throughput or a real CRM integration. The mock identity and persistence model need production-specific replacements and validation.

## Explore the implementation

| Resource | What to inspect |
| --- | --- |
| [Reference runbook](reference/README.md) | Demo, HTTP service, evaluation commands and implementation limitations |
| [Workflow implementation](reference/workflow.py) | Authority checks, persistence, tool execution and recovery |
| [HTTP contract](reference/openapi.json) | The reference service's workflow endpoints |
| [Scenario cases](reference/cases.json) | Deterministic inputs and expected outcomes |
| [Automated tests](tests/) | Workflow and authority regression coverage |
| [Architecture](architecture.md) | Enterprise layers and integration boundaries |
| [Agent flow](agent-flow.md) | Routing and execution design |
| [Illustrative API payload](api-example.json) | Broader architecture example; separate from the runnable HTTP contract |

## Engineering context

The wider design connects client interfaces, APIs, specialist agents and controlled tools to enterprise systems. Applications include sales/support workflows, internal knowledge assistants and process automation.

The key design choices are explicit routing, permissions enforced outside the model, durable workflow state, and separation between reasoning and external effects. A stateless conversation API can still depend on persistent approvals, audit records and recovery state.

## About

Richard Russell · Founder, [AI Venture X](https://aiventurex.com/)

[Technical profile and featured work](https://github.com/Poochaman#featured-work) · [Discuss an integration](https://aiventurex.com/#contact-2) · [LinkedIn](https://www.linkedin.com/in/richie-russell/)
