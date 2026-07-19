import { BookOpen, Check, FilePlus } from "@phosphor-icons/react";

import type { WorkflowStep } from "../domain/course";

interface WorkflowHeaderProps {
  step: WorkflowStep;
  hasChapters: boolean;
  canTeach: boolean;
  onNavigate(step: WorkflowStep): void;
  onStartNew(): void;
}

const workflowSteps: Array<{ id: WorkflowStep; label: string }> = [
  { id: "import", label: "导入资料" },
  { id: "generate", label: "生成课程" },
  { id: "edit", label: "编辑验证" },
  { id: "teach", label: "双屏授课" },
];

function navigationDisabled(
  target: WorkflowStep,
  activeIndex: number,
  hasChapters: boolean,
  canTeach: boolean,
): boolean {
  if (target === "import") {
    return false;
  }
  if (target === "generate") {
    return activeIndex < 1;
  }
  if (target === "edit") {
    return !hasChapters;
  }
  return !canTeach;
}

export function WorkflowHeader({
  step,
  hasChapters,
  canTeach,
  onNavigate,
  onStartNew,
}: WorkflowHeaderProps) {
  const activeIndex = workflowSteps.findIndex((item) => item.id === step);

  return (
    <header className="workflow-header">
      <div className="workflow-brand">
        <BookOpen aria-hidden="true" size={28} weight="duotone" />
        <span>课程工作台</span>
      </div>

      <nav className="workflow-steps" aria-label="课程工作流">
        {workflowSteps.map((item, index) => {
          const active = item.id === step;
          const completed = index < activeIndex;
          return (
            <button
              key={item.id}
              type="button"
              className={`workflow-step${active ? " is-active" : ""}${
                completed ? " is-completed" : ""
              }`}
              aria-label={item.label}
              aria-current={active ? "step" : undefined}
              disabled={navigationDisabled(
                item.id,
                activeIndex,
                hasChapters,
                canTeach,
              )}
              onClick={() => onNavigate(item.id)}
            >
              <span className="workflow-step__badge" aria-hidden="true">
                {completed ? <Check size={17} weight="bold" /> : index + 1}
              </span>
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      <button
        type="button"
        className="icon-button workflow-new"
        aria-label="新建课程"
        title="新建课程"
        onClick={onStartNew}
      >
        <FilePlus aria-hidden="true" size={22} weight="bold" />
      </button>
    </header>
  );
}
