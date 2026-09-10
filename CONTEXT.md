# Hearth

Hearth is a home for persistent agents doing useful work under human supervision.

## Language

**Resident:** An enduring agent identity with purpose, permissions, and memory.
_Avoid_: Bot, worker (when referring to the enduring identity).

**Skill text:** Instructions describing how a resident should approach work.
Skill text does not itself grant permission to act.

**Memory:** Knowledge a resident retains across tasks.

**Task:** Requested work with a desired outcome, potentially requiring multiple attempts.
_Avoid_: Job.

**Run:** One attempt to perform a task. Its identity is distinct from the task.
_Avoid_: Session (when referring to task execution).

**Routine:** A recurring source of tasks with an explicit schedule.

**Letter:** One resident's bounded question to another, worked by the receiver as an
ordinary task and answered by a reply the sender reads on its next run.
_Avoid_: Delegation, message, chat.

**Notification:** A durable record that Hearth told the household something. It is
read or unread and is never deleted.
_Avoid_: Delivery, alert.

**Artifact:** An output of work with a durable reference and provenance.

**Observation:** Evidence about activity or presence, associated with its source and freshness.

**Runtime:** The provider a resident's work is admitted to and settled under. Two are
live — the Codex subscription and the Claude subscription — and a resident declares
which one it runs on, so one household may hold both. A finished run keeps the runtime
it was worked by.
_Avoid_: Model, backend, engine.

**Burrow:** A machine on which residents execute.

**Hamlet:** Hearth's visual village view, showing real resident activity.

**Townhall:** Hearth's operator view for assigning work and governing residents.
