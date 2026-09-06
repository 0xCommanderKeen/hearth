import { Fragment, type ReactNode } from "react";

// Deliberately small Markdown vocabulary: headings, lists, paragraphs, code and emphasis.
// Every source byte is a React text child. HTML, images and links grant no execution/navigation.
function inline(text: string): ReactNode {
  return text
    .split(/(`[^`]+`|\*\*[^*]+\*\*)/g)
    .map((part, i) =>
      part.startsWith("`") && part.endsWith("`") ? (
        <code key={i}>{part.slice(1, -1)}</code>
      ) : part.startsWith("**") && part.endsWith("**") ? (
        <strong key={i}>{part.slice(2, -2)}</strong>
      ) : (
        <Fragment key={i}>{part}</Fragment>
      ),
    );
}
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code: string[] = [];
      while (++i < lines.length && !lines[i].startsWith("```"))
        code.push(lines[i]);
      blocks.push(
        <pre key={i}>
          <code>{code.join("\n")}</code>
        </pre>,
      );
    } else if (/^#{1,6} /.test(line)) {
      const level = line.indexOf(" ");
      blocks.push(
        <div
          role="heading"
          aria-level={level}
          className={`markdown-heading heading-${level}`}
          key={i}
        >
          {inline(line.slice(level + 1))}
        </div>,
      );
    } else if (/^[-*] /.test(line)) {
      const items = [line.slice(2)];
      while (i + 1 < lines.length && /^[-*] /.test(lines[i + 1]))
        items.push(lines[++i].slice(2));
      blocks.push(
        <ul key={i}>
          {items.map((item, j) => (
            <li key={j}>{inline(item)}</li>
          ))}
        </ul>,
      );
    } else if (line.trim()) {
      const paragraph = [line];
      while (
        i + 1 < lines.length &&
        lines[i + 1].trim() &&
        !/^(#{1,6} |[-*] |```)/.test(lines[i + 1])
      )
        paragraph.push(lines[++i]);
      blocks.push(<p key={i}>{inline(paragraph.join("\n"))}</p>);
    }
  }
  return <div className="skill-markdown">{blocks}</div>;
}
