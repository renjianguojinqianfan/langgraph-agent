export interface PreviewFailureCopy {
  title: string;
  hint: string;
}

/** FastAPI `detail` on a 404 that means the task record, not the file, is gone
 * (`backend/api/routes.py`: "task not found" / "file not found"). The preview
 * endpoint has no other 404 sources, so this one token splits the two copies. */
const TASK_NOT_FOUND_DETAIL = "task not found";

/**
 * Say why a preview failed and what to do next. The server already separates
 * these causes by status; flattening them into one line was the #266 bug.
 */
export function previewFailure(status: number, detail?: string): PreviewFailureCopy {
  switch (status) {
    case 0:
      return { title: "无法连接后台", hint: "确认服务在运行后重试。" };
    case 400:
      return {
        title: "路径超出任务工作区",
        hint: "预览被拒绝；请核对上方路径，再从文件列表重新选择。",
      };
    case 401:
      return {
        title: "登录已失效",
        hint: "重新登录后再试；预览和下载都需要有效登录。",
      };
    case 404:
      return detail?.includes(TASK_NOT_FOUND_DETAIL)
        ? { title: "任务不存在", hint: "任务记录已被删除；返回任务列表。" }
        : {
            title: "文件不存在",
            hint: "可能已被删除或移动；刷新文件列表后再试。",
          };
    case 409:
      return {
        title: "任务工作区不存在",
        hint: "检查任务目录是否还在；切回存在的目录或新建任务后再试。",
      };
    case 415:
      return {
        title: "二进制文件无法预览",
        hint: "直接下载后用本地应用打开。",
      };
    case 200:
      return {
        title: "预览响应异常",
        hint: "后台返回了无法解析的内容；请重试或直接下载。",
      };
    default:
      return {
        title: `预览失败（HTTP ${status}）`,
        hint: detail?.trim() || "稍后重试，或直接下载。",
      };
  }
}
