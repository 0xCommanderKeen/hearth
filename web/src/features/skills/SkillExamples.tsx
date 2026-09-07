import type { SkillAuthoring, SkillExample } from "../../shared/client";

export const exampleTemplate = (): SkillAuthoring => ({
  examples: [
    {
      kind: "normal",
      instruction: "Summarize the fictional orchard notes.",
      notes: ["Fictional orchard harvested 12 pears."],
      assertions: {
        max_characters: 1000,
        contains: ["12 pears"],
        excludes: [],
      },
    },
    {
      kind: "edge",
      instruction: "Report honestly when no notes were supplied.",
      notes: [],
      assertions: {
        max_characters: 1000,
        contains: ["No synthetic inputs"],
        excludes: ["PRIVATE_SENTINEL"],
      },
    },
  ],
});

export function cleanExamples(value: SkillAuthoring): SkillAuthoring {
  return {
    examples: value.examples.map((example) => ({
      ...example,
      notes: example.notes.filter((note) => note.trim()),
      assertions: {
        ...example.assertions,
        contains: example.assertions.contains.filter((phrase) => phrase.trim()),
        excludes: example.assertions.excludes.filter((phrase) => phrase.trim()),
      },
    })),
  };
}

export function SkillExamples({
  value,
  onChange,
}: {
  value: SkillAuthoring;
  onChange: (value: SkillAuthoring) => void;
}) {
  function update(position: number, changed: Partial<SkillExample>) {
    onChange({
      examples: value.examples.map((example, index) =>
        index === position ? { ...example, ...changed } : example,
      ),
    });
  }
  return (
    <section
      className="skill-example-editor"
      aria-label="Synthetic example definitions"
    >
      <span className="eyebrow">EXAMPLES THAT ACTUALLY RUN</span>
      <h3>A normal case. A difficult case.</h3>
      <p>
        These fictional inputs are executed with this exact skill in a read-only
        evaluator. Assertions check saved output; they do not replace execution
        or prove broad quality.
      </p>
      <div className="skill-example-grid">
        {value.examples.map((example, position) => {
          const label = example.kind === "normal" ? "Normal" : "Edge";
          return (
            <div className="skill-example-definition" key={example.kind}>
              <h4>{label} example</h4>
              <label>
                {label} task
                <textarea
                  required
                  maxLength={4000}
                  value={example.instruction}
                  onChange={(e) =>
                    update(position, { instruction: e.target.value })
                  }
                />
              </label>
              <label>
                {label} synthetic notes
                <textarea
                  maxLength={8003}
                  value={example.notes.join("\n")}
                  onChange={(e) =>
                    update(position, { notes: e.target.value.split("\n") })
                  }
                />
              </label>
              <small>
                Up to four notes, one per line, 2,000 characters each. Empty
                notes test missing input.
              </small>
              <label>
                {label} required phrases
                <textarea
                  maxLength={803}
                  value={example.assertions.contains.join("\n")}
                  onChange={(e) =>
                    update(position, {
                      assertions: {
                        ...example.assertions,
                        contains: e.target.value.split("\n"),
                      },
                    })
                  }
                />
              </label>
              <label>
                {label} forbidden phrases
                <textarea
                  maxLength={803}
                  value={example.assertions.excludes.join("\n")}
                  onChange={(e) =>
                    update(position, {
                      assertions: {
                        ...example.assertions,
                        excludes: e.target.value.split("\n"),
                      },
                    })
                  }
                />
              </label>
              <small>
                Up to four exact phrases per field, one per line, 200 characters
                each.
              </small>
              <label>
                {label} output character limit
                <input
                  required
                  type="number"
                  min="1"
                  max="8000"
                  value={example.assertions.max_characters}
                  onChange={(e) =>
                    update(position, {
                      assertions: {
                        ...example.assertions,
                        max_characters: Number(e.target.value),
                      },
                    })
                  }
                />
              </label>
            </div>
          );
        })}
      </div>
    </section>
  );
}
