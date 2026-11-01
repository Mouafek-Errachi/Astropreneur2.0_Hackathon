# HRplus diagrams

Eleven diagrams that explain HRplus, from the big picture to the details. Each page has one diagram (drawn
automatically by GitHub), a one-sentence summary and a short "how to read it".

**Short on time?** Read 01, 02 and 05: who uses the system, why the AI never acts on its own, and how attacks hidden
in CVs are stopped.

## The big picture
| # | Diagram | What it answers |
|---|---|---|
| 01 | [System context](01-system-context.md) | Who uses HRplus, what comes in from outside, and the five layers |
| 06 | [Five-layer architecture](06-five-layer-architecture.md) | What lives in each layer and how the layers talk |
| 07 | [Roles and permissions](07-roles-and-permissions.md) | What a recruiter, a manager and an admin can each do |

## Trust and security
| # | Diagram | What it answers |
|---|---|---|
| 02 | [Human approval state machine](02-human-approval-state-machine.md) | How every AI action waits for a signed human approval |
| 05 | [AI security guardrails](05-ai-security-guardrails.md) | How files and messages are inspected before any AI reads them |

## Main workflows
| # | Diagram | What it answers |
|---|---|---|
| 08 | [CV screening pipeline](08-cv-screening-pipeline.md) | How a CV becomes a ranked candidate and a human decision |
| 03 | [Skill gap workflow](03-skill-gap-workflow.md) | How a project's needs become internal options or a job offer |
| 04 | [Workforce analytics data flow](04-workforce-analytics-data-flow.md) | How HR records become dashboard charts, privacy included |
| 09 | [Interview Copilot](09-interview-copilot.md) | How live interview help works and what is stored |

## Under the hood
| # | Diagram | What it answers |
|---|---|---|
| 10 | [Data model](10-data-model.md) | The main records and how they connect |
| 11 | [Deployment](11-deployment.md) | What runs where, and what is plugged in through settings |

The numbers are stable references: "see diagram 05" always means the security guardrails, whatever order you read in.
