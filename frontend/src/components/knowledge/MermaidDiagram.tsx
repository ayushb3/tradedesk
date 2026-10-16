/**
 * MermaidDiagram — renders a Mermaid source string as an inline SVG.
 *
 * `mermaid` is lazy-imported (dynamic import) so it stays out of the initial
 * bundle and only loads on lesson pages that actually carry a diagram. The lib
 * is initialized once (dark theme, to match the app shell) and each diagram is
 * rendered via `mermaid.render`. On any parse/render failure we fall back to a
 * <pre> of the raw source so a malformed diagram never crashes the reader.
 *
 * Callers gate on `sheet.diagram_mermaid` being non-null before mounting this.
 */

import { useEffect, useId, useRef, useState } from "react";

/** Module-scoped guard so `mermaid.initialize` runs exactly once per session. */
let initialized = false;

interface MermaidDiagramProps {
  source: string;
}

export function MermaidDiagram({ source }: MermaidDiagramProps) {
  // useId gives a stable, DOM-safe unique id; mermaid needs one per render.
  const rawId = useId();
  const renderId = `mermaid-${rawId.replace(/[^a-zA-Z0-9]/g, "")}`;
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const activeRef = useRef(true);

  useEffect(() => {
    activeRef.current = true;
    setSvg(null);
    setFailed(false);

    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        if (!initialized) {
          mermaid.initialize({
            startOnLoad: false,
            theme: "dark",
            securityLevel: "strict",
            fontFamily: "inherit",
          });
          initialized = true;
        }
        const { svg: out } = await mermaid.render(renderId, source);
        if (activeRef.current) setSvg(out);
      } catch {
        // Malformed Mermaid, or the lib failed to load — degrade to raw source.
        if (activeRef.current) setFailed(true);
      }
    })();

    return () => {
      activeRef.current = false;
    };
  }, [source, renderId]);

  if (failed) {
    return (
      <pre className="overflow-x-auto whitespace-pre-wrap rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
        {source}
      </pre>
    );
  }

  if (svg == null) {
    // Brief loading placeholder while the lib + render resolve.
    return (
      <div className="h-24 animate-pulse rounded-md border border-border bg-muted/30" />
    );
  }

  return (
    <div
      className="flex justify-center overflow-x-auto [&_svg]:h-auto [&_svg]:max-w-full"
      // Mermaid output is sanitized (securityLevel: "strict") from trusted,
      // offline-generated study-sheet source.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
