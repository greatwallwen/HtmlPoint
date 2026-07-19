import {
  CheckCircle,
  ShieldCheck,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";

import {
  latestApplicableValidationReceipt,
  useWorkspace,
} from "../state/workspace";

export function ValidationPanel() {
  const { state, dispatch, validateCurrentCourse } = useWorkspace();
  const receipt = latestApplicableValidationReceipt(state);
  const running = state.validation === "running";
  const errors =
    receipt?.checks.filter((check) => check.level === "error") ?? [];
  const warnings =
    receipt?.checks.filter((check) => check.level === "warning") ?? [];
  const passes =
    receipt?.checks.filter((check) => check.level === "pass") ?? [];
  const findings = [...errors, ...warnings];
  const canAcknowledge = errors.length === 0 && warnings.length > 0;

  return (
    <section
      className="validation-panel"
      role="region"
      aria-labelledby="validation-panel-heading"
    >
      <header className="validation-panel__heading">
        <div>
          <p className="editor-panel-kicker">发布门槛</p>
          <h3 id="validation-panel-heading">课程验证</h3>
        </div>
        <button
          type="button"
          className="secondary-button validation-panel__run"
          disabled={running}
          onClick={() => void validateCurrentCourse()}
        >
          <ShieldCheck aria-hidden="true" size={19} weight="bold" />
          {running ? "正在验证…" : "验证课程"}
        </button>
      </header>

      {state.validation === "error" && state.validationError ? (
        <p className="validation-panel__error" role="alert">
          {state.validationError}
        </p>
      ) : null}

      {receipt ? (
        <div className="validation-panel__result">
          <p className="validation-panel__summary">{receipt.summary}</p>
          <div className="validation-panel__counts" aria-label="校验统计">
            <span className={errors.length > 0 ? "is-error" : undefined}>
              <XCircle aria-hidden="true" size={17} weight="fill" />
              {errors.length} 个错误
            </span>
            <span className={warnings.length > 0 ? "is-warning" : undefined}>
              <WarningCircle aria-hidden="true" size={17} weight="fill" />
              {warnings.length} 个警告
            </span>
            <span className="is-pass">
              <CheckCircle aria-hidden="true" size={17} weight="fill" />
              {passes.length} 项通过
            </span>
          </div>

          {findings.length > 0 ? (
            <ul className="validation-panel__findings">
              {findings.map((finding) => (
                <li key={finding.id} className={`is-${finding.level}`}>
                  {finding.level === "error" ? (
                    <XCircle aria-hidden="true" size={17} weight="fill" />
                  ) : (
                    <WarningCircle aria-hidden="true" size={17} weight="fill" />
                  )}
                  <span>{finding.message}</span>
                </li>
              ))}
            </ul>
          ) : null}

          <p className="validation-panel__passed-summary">
            已通过 {passes.length} 项检查
          </p>
          <p className="validation-panel__receipt">
            <span>校验收据</span>
            <code>{receipt.inputDigest.slice(0, 12)}</code>
          </p>

          {canAcknowledge ? (
            <label className="validation-panel__acknowledgement">
              <input
                type="checkbox"
                checked={state.validationWarningsAcknowledged}
                onChange={(event) =>
                  dispatch({
                    type: "ACKNOWLEDGE_VALIDATION_WARNINGS",
                    acknowledged: event.target.checked,
                  })
                }
              />
              <span>我已知悉校验警告，可以进入排练</span>
            </label>
          ) : null}

          <p className="validation-panel__gate-note">
            {errors.length > 0
              ? "错误会阻止进入授课，请先修正后重新验证。"
              : warnings.length > 0
                ? "确认已知悉警告后，可以进入排练。"
                : "所有校验门槛已通过，可以进入排练。"}
          </p>
        </div>
      ) : running ? (
        <p className="validation-panel__running" role="status">
          正在生成可复查的课程校验证据…
        </p>
      ) : (
        <p className="validation-panel__empty">验证后将在这里显示检查与收据。</p>
      )}
    </section>
  );
}
