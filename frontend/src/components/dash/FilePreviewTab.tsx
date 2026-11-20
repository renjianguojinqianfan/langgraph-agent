import { Fragment, useEffect, useMemo, useState, type CSSProperties } from "react";
import { previewFile } from "../../api/client";
import {
  highlightLines,
  type HighlightToken,
  type HighlightedLines,
} from "../../lib/codeHighlight";
import {
  breadcrumbForPath,
  emptyFileCopy,
  gutterDigits,
  languageForPath,
  lineNumbers,
  previewLines,
  previewNotices,
} from "../../lib/codePreview";
import { formatBytes } from "../../lib/format";
import { previewFailure, type PreviewFailureCopy } from "../../lib/preview";
import type { FilePreview } from "../../types";
import { Icon } from "../ui/Icon";
import { FileDownloadLink } from "./FileDownloadLink";

type State =
  | { phase: "loading" }
  | { phase: "ready"; preview: FilePreview }
  | { phase: "failed"; copy: PreviewFailureCopy };

interface FilePreviewTabProps {
  taskId: string;
  path: string;
  /** Fetch on first activation, not on mount: hidden chips cost no request. */
  active: boolean;
  /** Narrow-screen drawer: a closed drawer must not fetch (#230). */
  visible: boolean;
}

function tokenStyle(token: HighlightToken): CSSProperties | undefined {
  if (!token.color && !token.fontStyle) return undefined;
  const style: CSSProperties = {};
  if (token.color) style.color = token.color;
  if (token.fontStyle) {
    if (token.fontStyle & 1) style.fontStyle = "italic";
    if (token.fontStyle & 2) style.fontWeight = 600;
    if (token.fontStyle & 4) style.textDecoration = "underline";
  }
  return style;
}

/** One rendered line. The gutter is a separate column, so line numbers never
 * share a text node with (or enter a copy selection from) the code. */
function CodeLine({ text, tokens }: { text: string; tokens?: HighlightToken[] }) {
  const parts = tokens ?? [{ content: text, offset: 0 }];
  return (
    <span className="block min-h-[1lh] whitespace-pre">
      {parts.map((token, index) => (
        <span key={index} style={tokenStyle(token)}>
          {token.content}
        </span>
      ))}
    </span>
  );
}

function Breadcrumb({ path }: { path: string }) {
  const { dirs, name } = breadcrumbForPath(path);
  return (
    <nav
      aria-label="文件路径"
      className="flex min-w-0 items-center gap-1 overflow-hidden whitespace-nowrap font-mono text-machine"
    >
      {dirs.map((dir, index) => (
        <Fragment key={`${dir}-${index}`}>
          <span
            className="min-w-0 shrink truncate text-tertiary"
            title={dir === "…" ? undefined : dir}
          >
            {dir}
          </span>
          <span aria-hidden="true" className="shrink-0 text-tertiary">
            /
          </span>
        </Fragment>
      ))}
      <span className="min-w-0 shrink truncate text-primary" title={name}>
        {name}
      </span>
    </nav>
  );
}

/**
 * One file-preview tab (#266, #344): the third column switches to this body
 * instead of appending a preview below the file list. Read-only: breadcrumb,
 * size, download, line-number gutter and lazily highlighted code. The download
 * link is present in every state, including failures.
 */
export function FilePreviewTab({
  taskId,
  path,
  active,
  visible,
}: FilePreviewTabProps) {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [load, setLoad] = useState(active && visible);
  const [highlighted, setHighlighted] = useState<HighlightedLines | null>(null);

  useEffect(() => {
    if (active && visible) setLoad(true);
  }, [active, visible]);

  useEffect(() => {
    if (!load) return;
    let cancelled = false;
    setState({ phase: "loading" });
    previewFile(taskId, path)
      .then((result) => {
        if (cancelled) return;
        if (result.data) setState({ phase: "ready", preview: result.data });
        else setState({ phase: "failed", copy: previewFailure(result.status, result.detail) });
      })
      .catch(() => {
        if (!cancelled) setState({ phase: "failed", copy: previewFailure(0) });
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, path, load]);

  const content = state.phase === "ready" ? state.preview.content : null;
  const model = useMemo(() => (content === null ? null : previewLines(content)), [content]);
  const language = languageForPath(path);

  // Highlight only what is shown, after the plain text is already on screen.
  // A failed grammar/chunk must leave the readable fallback untouched.
  useEffect(() => {
    if (model === null || model.lines.length === 0) {
      setHighlighted(null);
      return;
    }
    let cancelled = false;
    setHighlighted(null);
    highlightLines(language, model.lines).then((tokens) => {
      if (!cancelled) setHighlighted(tokens);
    });
    return () => {
      cancelled = true;
    };
  }, [language, model]);

  const numbers = model ? lineNumbers(model.lines.length) : [];
  const notices = model
    ? previewNotices(state.phase === "ready" && state.preview.truncated, model.hiddenLines)
    : [];
  const empty = model && model.lines.length === 0 ? emptyFileCopy() : null;
  const rendered = model && highlighted?.length === model.lines.length ? highlighted : null;

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-2">
      <div className="flex min-w-0 shrink-0 items-center gap-2">
        <Icon name="file" size={14} className="text-tertiary" />
        <div className="min-w-0 flex-1" title={path}>
          <Breadcrumb path={path} />
        </div>
        {state.phase === "ready" && (
          <span className="shrink-0 font-mono text-machine tabular-nums text-tertiary">
            {formatBytes(state.preview.size)}
          </span>
        )}
        <FileDownloadLink taskId={taskId} path={path} />
      </div>

      {notices.length > 0 && (
        <div role="status" className="shrink-0">
          {notices.map((notice) => (
            <p key={notice} className="text-meta text-state-warn">
              {notice}
            </p>
          ))}
        </div>
      )}

      {state.phase === "loading" && (
        <p role="status" className="text-body text-tertiary">
          加载中…
        </p>
      )}

      {state.phase === "failed" && (
        <div role="alert" className="rounded-well border border-rule bg-surface-well px-3 py-3">
          <p className="text-body text-primary">{state.copy.title}</p>
          <p className="mt-1 text-meta text-tertiary">{state.copy.hint}</p>
          <FileDownloadLink taskId={taskId} path={path} labeled />
        </div>
      )}

      {state.phase === "ready" && model && empty && (
        <div className="min-h-0 flex-1 rounded-well border border-rule bg-surface-well px-3 py-6">
          <p className="text-body text-secondary">{empty.title}</p>
          <p className="mt-1 text-meta text-tertiary">{empty.hint}</p>
        </div>
      )}

      {state.phase === "ready" && model && model.lines.length > 0 && (
        <div
          role="region"
          aria-label={`${path} 文件内容`}
          tabIndex={0}
          className="min-h-0 flex-1 overflow-auto rounded-well border border-rule bg-surface-well"
        >
          <div className="flex w-max min-w-full">
            <div
              aria-hidden="true"
              className="sticky left-0 z-10 shrink-0 select-none border-r border-rule bg-surface-well px-2 py-2 text-right font-mono text-code tabular-nums text-tertiary"
              style={{ minWidth: `${gutterDigits(numbers.length) + 2}ch` }}
            >
              {numbers.map((number) => (
                <div key={number} className="min-h-[1lh]">
                  {number}
                </div>
              ))}
            </div>
            <pre className="w-max shrink-0 px-3 py-2 font-mono text-code text-secondary [tab-size:2]">
              {model.lines.map((line, index) => (
                <CodeLine key={index} text={line} tokens={rendered?.[index]} />
              ))}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
