# CritPT production supervision

CritPT production uses a strict reporting hierarchy:

```text
owner
  ↑
supervisor
  ↑
secretary mailbox and periodic digest
  ↑
one-task subordinate workers
```

The subordinate is the production subject. A worker session reports progress,
blockers, question requests and completion evidence to the secretary mailbox;
it does not message the Supervisor for every heartbeat or intermediate step.
The secretary collects reports for a bounded interval, removes duplicate
heartbeats, keeps the newest evidence pointer per worker, and sends one digest
upward. A blocked/failed/question report may trigger an early digest, but still
does not create one Supervisor turn per worker.

The Supervisor remains the only management decision-maker below the owner. It
may coordinate active workers, but may not turn a report into acceptance,
termination, candidate closure, source replacement or rollout approval without
the required evidence and owner decision. Owner gates stop worker dispatch and
secretary upward delivery; reports remain in the mailbox for audit.

The Web management view shows only the Supervisor, Secretary mailbox status and
active top-level workers. Child helpers are visible inside their parent's
detail view. Completed, fired, shelved and abandoned items leave the live view;
their immutable receipts remain outside the live queue and must not be reopened
by the secretary.

The secretary is a traffic-control layer, not a scientific reviewer. It must
preserve task IDs, employee IDs, report paths, SHA-256 values and status
distinctions. It may summarize transport and scheduling facts, but it must not
rewrite physics, scores, ground truth, source rights or model outcomes.
