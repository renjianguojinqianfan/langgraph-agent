import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

type HastNode = { position?: { start?: { line?: number } } };

function headingId(node: unknown, ids?: Map<number, string>): string | undefined {
  const line = (node as HastNode | undefined)?.position?.start?.line;
  return line != null ? ids?.get(line) : undefined;
}

/**
 * Answer / plan body. Raw HTML stays inert (`skipHtml`, no `rehype-raw`) and
 * h1 is remapped to h2 so the task title keeps the page's only h1. `headingIds`
 * pins ids by source line (from `parsePlanToc`) for the plan's table of contents.
 */
function buildComponents(headingIds?: Map<number, string>): Components {
  return {
    h1: ({ children, node }) => (
      <h2
        id={headingId(node, headingIds)}
        className="mb-2 mt-4 text-title text-primary"
      >
        {children}
      </h2>
    ),
    h2: ({ children, node }) => (
      <h2
        id={headingId(node, headingIds)}
        className="mb-2 mt-4 text-title text-primary"
      >
        {children}
      </h2>
    ),
    h3: ({ children, node }) => (
      <h3
        id={headingId(node, headingIds)}
        className="mb-1.5 mt-3 text-body font-semibold text-primary"
      >
        {children}
      </h3>
    ),
    p: ({ children }) => <p className="my-2 text-body text-primary">{children}</p>,
    ul: ({ children }) => (
      <ul className="my-2 list-disc pl-5 text-body text-primary [&>li+li]:mt-0.5">
        {children}
      </ul>
    ),
    ol: ({ children }) => (
      <ol className="my-2 list-decimal pl-5 text-body text-primary [&>li+li]:mt-0.5">
        {children}
      </ol>
    ),
    li: ({ children }) => <li className="pl-0.5">{children}</li>,
    blockquote: ({ children }) => (
      <blockquote className="my-2 border-l-2 border-rule-strong pl-3 text-body text-secondary">
        {children}
      </blockquote>
    ),
    a: ({ children, href }) => (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="text-signal-now underline underline-offset-2"
      >
        {children}
      </a>
    ),
    pre: ({ children }) => (
      <pre className="my-2 max-h-[420px] overflow-auto rounded-well bg-surface-well p-3 font-mono text-code text-secondary">
        {children}
      </pre>
    ),
    code: ({ className, children }) => (
      <code
        className={
          className
            ? "font-mono text-code"
            : "rounded bg-surface-well px-1 py-0.5 font-mono text-code text-primary"
        }
      >
        {children}
      </code>
    ),
    table: ({ children }) => (
      <div className="my-2 overflow-x-auto">
        <table className="w-full border-collapse text-meta">{children}</table>
      </div>
    ),
    th: ({ children }) => (
      <th className="border-b border-rule-strong px-2 py-1 text-left font-semibold text-secondary">
        {children}
      </th>
    ),
    td: ({ children }) => (
      <td className="border-b border-rule px-2 py-1 text-secondary">{children}</td>
    ),
    hr: () => <hr className="my-4 border-rule" />,
    strong: ({ children }) => (
      <strong className="font-semibold text-primary">{children}</strong>
    ),
  };
}

export function Markdown({
  children,
  headingIds,
}: {
  children: string;
  headingIds?: Map<number, string>;
}) {
  return (
    <div className="text-body text-primary [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={buildComponents(headingIds)}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
