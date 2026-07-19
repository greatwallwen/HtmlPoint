import { PaperPlaneRight, Sparkle } from "@phosphor-icons/react";
import { useState, type FormEvent, type KeyboardEvent } from "react";

import { useWorkspace } from "../state/workspace";

const supportedIntents = [
  "缩短课程到 90 分钟",
  "为本章补充案例",
  "检查来源覆盖",
] as const;

export function AssistantDock() {
  const { state, applyAssistantIntent } = useWorkspace();
  const [intent, setIntent] = useState("");
  const running = state.assistant === "running";
  const latestReceipt =
    state.assistant === "success" && state.assistantReceiptId
      ? state.receipts.find(
          (receipt) => receipt.id === state.assistantReceiptId,
        )
      : undefined;

  const submitIntent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (running) {
      return;
    }
    const recognized = await applyAssistantIntent(intent);
    if (recognized) {
      setIntent("");
    }
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  return (
    <section
      className="assistant-dock"
      role="region"
      aria-labelledby="assistant-dock-heading"
    >
      <div className="assistant-dock__summary">
        <div className="assistant-dock__title">
          <Sparkle aria-hidden="true" size={22} weight="duotone" />
          <div>
            <p className="editor-panel-kicker">协同调整</p>
            <h2 id="assistant-dock-heading">课程助手</h2>
          </div>
        </div>

        {running ? (
          <p className="assistant-dock__message" role="status">
            课程助手正在处理…
          </p>
        ) : state.assistant === "error" && state.assistantError ? (
          <p className="assistant-dock__message is-error" role="alert">
            {state.assistantError}
          </p>
        ) : state.assistantMessage ? (
          <p className="assistant-dock__message" role="status">
            {state.assistantMessage}
          </p>
        ) : (
          <p className="assistant-dock__hint">选择建议，或直接说明要调整的内容。</p>
        )}

        {latestReceipt ? (
          <p className="assistant-dock__receipt">
            <span>助手收据</span>
            <code>{latestReceipt.inputDigest.slice(0, 12)}</code>
          </p>
        ) : null}
      </div>

      <form
        className="assistant-dock__form"
        aria-label="课程助手"
        onSubmit={(event) => void submitIntent(event)}
      >
        <div
          className="assistant-dock__suggestions"
          role="group"
          aria-label="课程助手建议"
        >
          {supportedIntents.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              disabled={running}
              onClick={() => setIntent(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>

        <div className="assistant-dock__composer">
          <label htmlFor="assistant-intent">向课程助手说明调整需求</label>
          <textarea
            id="assistant-intent"
            value={intent}
            rows={2}
            disabled={running}
            onChange={(event) => setIntent(event.target.value)}
            onKeyDown={handleComposerKeyDown}
          />
          <button
            type="submit"
            className="icon-button assistant-dock__send"
            aria-label="发送给课程助手"
            title="发送给课程助手"
            disabled={running}
          >
            <PaperPlaneRight aria-hidden="true" size={22} weight="bold" />
          </button>
        </div>
      </form>
    </section>
  );
}
