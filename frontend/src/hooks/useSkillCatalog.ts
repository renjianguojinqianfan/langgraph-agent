import { useCallback, useEffect, useState } from "react";
import { listSkills } from "../api/client";
import {
  skillCatalogFailure,
  type SkillCatalogSnapshot,
} from "../lib/skillCatalog";

const LOADING: SkillCatalogSnapshot = {
  phase: "loading",
  enabled: true,
  skills: [],
  error: "",
};

/**
 * #345: one catalog read per task / workspace context. Failures stay in the
 * snapshot (never throw into the composer); `reload` drives the skill group's
 * retry row. The built-in `/` commands have no dependency on this hook.
 */
export function useSkillCatalog(
  taskId: string | null,
  workspaceDir: string
): { catalog: SkillCatalogSnapshot; reload: () => void } {
  const [catalog, setCatalog] = useState<SkillCatalogSnapshot>(LOADING);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setCatalog((current) => ({ ...current, phase: "loading", error: "" }));
    void listSkills(
      taskId ? { taskId } : workspaceDir ? { workspace: workspaceDir } : {},
      controller.signal
    ).then((result) => {
      if (controller.signal.aborted) return;
      if (result.data) {
        setCatalog({
          phase: "ready",
          enabled: result.data.enabled,
          skills: result.data.skills,
          error: "",
        });
        return;
      }
      setCatalog({
        phase: "error",
        enabled: false,
        skills: [],
        error: skillCatalogFailure(result.status, result.detail),
      });
    });
    return () => controller.abort();
  }, [taskId, workspaceDir, revision]);

  const reload = useCallback(() => setRevision((value) => value + 1), []);
  return { catalog, reload };
}
