import { fileDownloadUrl } from "../../api/client";
import { Icon } from "../ui/Icon";

interface FileDownloadLinkProps {
  taskId: string;
  path: string;
  /** The failure box spells the action out; header and list rows stay icon-only. */
  labeled?: boolean;
}

export function FileDownloadLink({ taskId, path, labeled = false }: FileDownloadLinkProps) {
  const href = fileDownloadUrl(taskId, path);
  if (labeled) {
    return (
      <a
        href={href}
        download
        className="mt-2 inline-flex items-center gap-1.5 rounded-control border border-rule px-2 py-1 text-meta text-primary hover:bg-surface-hover"
      >
        <Icon name="download" size={14} />
        下载文件
      </a>
    );
  }
  return (
    <a
      href={href}
      download
      aria-label={`下载 ${path}`}
      title="下载"
      className="shrink-0 rounded-control p-1.5 text-tertiary hover:bg-surface-hover hover:text-primary"
    >
      <Icon name="download" size={14} />
    </a>
  );
}
