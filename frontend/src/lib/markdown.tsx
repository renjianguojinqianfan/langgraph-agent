import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Answer / plan body. Raw HTML stays inert (`skipHtml`, no `rehype-raw`) and
 * h1 is remapped to h2 so the task title keeps the page's only h1.
 */
const components: Components = {
  h1: ({ children }) => (
    <h2 className="mb-2 mt-4 text-title text-primary">{children}</h2>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-4 text-title text-primary">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1.5 mt-3 text-body font-semibold text-primary">
      {children}
    </h3>
  ),
  p: ({ children }) => <p className="my-2 text-body text-primary">{children}</p>,
  ul: ({ children }) => (
    <ul className="my-2 list-disc space-y-0.5 pl-5 text-body text-primary">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="my-2 list-decimal space-y-0.5 pl-5 text-body text-primary">
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

export function Markdown({ children }: { children: string }) {
  return (
    <div className="text-body text-primary [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
