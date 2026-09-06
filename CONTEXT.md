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

**Approval:** A human decision about one specific proposed action.
_Avoid_: Permission to continue.

**Artifact:** An output of work with a durable reference and provenance.

**Observation:** Evidence about activity or presence, associated with its source and freshness.

**Burrow:** A machine on which residents execute.

**Hamlet:** Hearth's visual village view, showing real resident activity.

**Townhall:** Hearth's operator view for assigning work and governing residents.
