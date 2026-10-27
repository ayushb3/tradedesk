import { parseInline, parseMarkdown } from "@/lib/learn";

function Inline({ text }: { text: string }) {
  return (
    <>
      {parseInline(text).map((s, i) =>
        s.code ? (
          <code key={i} className="num rounded bg-muted px-1 text-label">
            {s.text}
          </code>
        ) : s.bold ? (
          <strong key={i} className="font-semibold">
            {s.text}
          </strong>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}

/** Headings, bullets, paragraphs, bold and code. Always text nodes, never raw HTML. */
export function Markdown({ md }: { md: string }) {
  const blocks = parseMarkdown(md);
  if (!blocks.length) return null;
  return (
    <div className="flex flex-col gap-2 text-body text-foreground/90">
      {blocks.map((b, i) =>
        b.kind === "h" ? (
          <h4 key={i} className="text-body font-semibold">
            <Inline text={b.text} />
          </h4>
        ) : b.kind === "ul" ? (
          <ul key={i} className="list-disc space-y-1 pl-5">
            {b.items.map((it, j) => (
              <li key={j}>
                <Inline text={it} />
              </li>
            ))}
          </ul>
        ) : (
          <p key={i}>
            <Inline text={b.text} />
          </p>
        ),
      )}
    </div>
  );
}
