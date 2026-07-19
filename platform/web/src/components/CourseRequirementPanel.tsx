import type { FormEvent, JSX } from "react";

import type {
  CourseRequirementDraft,
  CourseUsageScope,
} from "../domain/course-agent";

export interface CourseTagOption {
  id: string;
  label: string;
  dimension: string;
}

export interface CourseRequirementPanelProps {
  value: CourseRequirementDraft;
  tagOptions: CourseTagOption[];
  disabled?: boolean;
  error?: string;
  fieldErrors?: Partial<
    Record<"title" | "audience" | "learningGoals" | "durationMinutes", string>
  >;
  submitLabel: string;
  onChange(value: CourseRequirementDraft): void;
  onSubmit(): void;
}

export function CourseRequirementPanel({
  value,
  tagOptions,
  disabled = false,
  error,
  fieldErrors = {},
  submitLabel,
  onChange,
  onSubmit,
}: CourseRequirementPanelProps): JSX.Element {
  const patch = (next: Partial<CourseRequirementDraft>) =>
    onChange({ ...value, ...next });
  const toggleTag = (tagId: string, mode: "required" | "excluded") => {
    const required = new Set(value.requiredTagIds);
    const excluded = new Set(value.excludedTagIds);
    const target = mode === "required" ? required : excluded;
    const other = mode === "required" ? excluded : required;
    if (target.has(tagId)) {
      target.delete(tagId);
    } else {
      target.add(tagId);
      other.delete(tagId);
    }
    patch({
      requiredTagIds: [...required],
      excludedTagIds: [...excluded],
    });
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit();
  };

  return (
    <form className="brief-form requirement-panel" noValidate onSubmit={submit}>
      <div className="form-field form-field--wide">
        <label htmlFor="course-title">课程名称</label>
        <input
          id="course-title"
          value={value.title}
          disabled={disabled}
          onChange={(event) => patch({ title: event.target.value })}
        />
        {fieldErrors.title ? <span className="field-error">{fieldErrors.title}</span> : null}
      </div>

      <div className="form-field">
        <label htmlFor="course-audience">课程受众</label>
        <input
          id="course-audience"
          value={value.audience}
          disabled={disabled}
          onChange={(event) => patch({ audience: event.target.value })}
        />
        {fieldErrors.audience ? <span className="field-error">{fieldErrors.audience}</span> : null}
      </div>

      <div className="form-field">
        <label htmlFor="course-duration">课程时长（分钟）</label>
        <input
          id="course-duration"
          type="number"
          min={40}
          max={480}
          step={5}
          value={value.durationMinutes}
          disabled={disabled}
          onChange={(event) => patch({ durationMinutes: Number(event.target.value) })}
        />
        {fieldErrors.durationMinutes ? (
          <span className="field-error">{fieldErrors.durationMinutes}</span>
        ) : null}
      </div>

      <div className="form-field form-field--wide">
        <label htmlFor="course-learning-goals">课程目标</label>
        <small>每行填写一个学习目标</small>
        <textarea
          id="course-learning-goals"
          rows={5}
          value={value.learningGoals.join("\n")}
          disabled={disabled}
          onChange={(event) =>
            patch({ learningGoals: event.target.value.split(/\r?\n/) })
          }
        />
        {fieldErrors.learningGoals ? (
          <span className="field-error">{fieldErrors.learningGoals}</span>
        ) : null}
      </div>

      <div className="form-field">
        <label htmlFor="course-usage-scope">使用范围</label>
        <select
          id="course-usage-scope"
          value={value.usageScope}
          disabled={disabled}
          onChange={(event) =>
            patch({ usageScope: event.target.value as CourseUsageScope })
          }
        >
          <option value="private-training">个人培训</option>
          <option value="internal">组织内部</option>
          <option value="public">公开发布</option>
        </select>
      </div>

      <fieldset className="form-field requirement-options">
        <legend>内容要求</legend>
        <label>
          <input
            type="checkbox"
            checked={value.requireVisualRefs}
            disabled={disabled}
            onChange={(event) => patch({ requireVisualRefs: event.target.checked })}
          />
          需要真实视觉资料
        </label>
        <label>
          <input
            type="checkbox"
            checked={value.requireDatasetRefs}
            disabled={disabled}
            onChange={(event) => patch({ requireDatasetRefs: event.target.checked })}
          />
          需要数据集证据
        </label>
      </fieldset>

      <fieldset className="form-field form-field--wide requirement-tags">
        <legend>受控标签</legend>
        {tagOptions.length === 0 ? (
          <p>暂无可选标签，可先按学习目标组合。</p>
        ) : (
          <ul>
            {tagOptions.map((tag) => (
              <li key={tag.id}>
                <span>
                  <strong>{tag.label}</strong>
                  <small>{tag.dimension}</small>
                </span>
                <label>
                  <input
                    type="checkbox"
                    checked={value.requiredTagIds.includes(tag.id)}
                    disabled={disabled}
                    onChange={() => toggleTag(tag.id, "required")}
                  />
                  必选
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={value.excludedTagIds.includes(tag.id)}
                    disabled={disabled}
                    onChange={() => toggleTag(tag.id, "excluded")}
                  />
                  排除
                </label>
              </li>
            ))}
          </ul>
        )}
      </fieldset>

      {error ? (
        <p className="operation-error form-field--wide" role="alert">
          {error}
        </p>
      ) : null}

      <div className="panel-actions form-field--wide">
        <button type="submit" className="primary-button" disabled={disabled}>
          {submitLabel}
        </button>
      </div>
    </form>
  );
}
