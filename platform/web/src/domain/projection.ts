import type {
  ProjectionCommand,
  ProjectionReceipt,
} from "./projection-schema";

export interface ProjectionIdentity {
  courseVersionId: string;
  slideDeckId: string;
  runtimeManifestId: string;
  runtimeManifestDigest: string;
}

export type ProjectionStepState = "waiting" | "active" | "complete" | "attention";

export interface ProjectionSteps {
  detect: ProjectionStepState;
  assign: ProjectionStepState;
  fullscreen: ProjectionStepState;
  witness: ProjectionStepState;
}

export type ProjectionSetupStatus =
  | "idle"
  | "running"
  | "witness-ready"
  | "witness-pending"
  | "certified"
  | "invalidated"
  | "error"
  | "closed";

export type ProjectionAssignmentLabel = "external-stage" | "internal-stage";

export type ProjectionErrorCode =
  | "runtime-unavailable"
  | "host-unavailable"
  | "topology-ineligible"
  | "fullscreen-failed"
  | "content-unavailable"
  | "stale-response"
  | "command-rejected"
  | "command-failed";

export interface ProjectionPendingCommand {
  commandId: string;
  command: ProjectionCommand["command"];
  sessionId: string | null;
  expectedGeneration: number;
}

export interface ProjectionSetupState {
  identity: ProjectionIdentity;
  status: ProjectionSetupStatus;
  generation: number;
  assignment: ProjectionAssignmentLabel;
  steps: ProjectionSteps;
  physicalDualScreenCertified: boolean;
  releaseSignatureCertified: false;
  pending?: ProjectionPendingCommand;
  swapPending?: boolean;
  error?: ProjectionErrorCode;
}

export type ProjectionSetupAction =
  | {
      type: "COMMAND_STARTED";
      pending: ProjectionPendingCommand;
      swap?: boolean;
    }
  | {
      type: "RECEIPT_RECEIVED";
      identity: ProjectionIdentity;
      receipt: ProjectionReceipt;
    }
  | { type: "COMMAND_FAILED"; code: ProjectionErrorCode }
  | { type: "WITNESS_INVALIDATED" }
  | { type: "RESET" };

const waitingSteps = (): ProjectionSteps => ({
  detect: "waiting",
  assign: "waiting",
  fullscreen: "waiting",
  witness: "waiting",
});

export const initialProjectionSetup = (
  identity: ProjectionIdentity,
): ProjectionSetupState => ({
  identity,
  status: "idle",
  generation: 0,
  assignment: "external-stage",
  steps: waitingSteps(),
  physicalDualScreenCertified: false,
  releaseSignatureCertified: false,
});

const identityMatches = (
  left: ProjectionIdentity,
  right: ProjectionIdentity,
): boolean =>
  left.courseVersionId === right.courseVersionId &&
  left.slideDeckId === right.slideDeckId &&
  left.runtimeManifestId === right.runtimeManifestId &&
  left.runtimeManifestDigest === right.runtimeManifestDigest;

const activeStep = (
  steps: ProjectionSteps,
  command: ProjectionPendingCommand["command"],
): ProjectionSteps => {
  if (command === "detect_displays") return { ...steps, detect: "active" };
  if (command === "open_projection_session" || command === "assign_projection_window") {
    return { ...steps, assign: "active" };
  }
  if (command === "enter_projection_fullscreen") {
    return { ...steps, fullscreen: "active" };
  }
  if (command === "verify_projection_assignment") {
    return { ...steps, witness: "active" };
  }
  return steps;
};

const invalidated = (
  state: ProjectionSetupState,
  code: ProjectionErrorCode = "stale-response",
): ProjectionSetupState => ({
  ...state,
  status: "invalidated",
  pending: undefined,
  error: code,
  physicalDualScreenCertified: false,
  steps: {
    ...state.steps,
    witness: state.steps.witness === "complete" ? "attention" : state.steps.witness,
  },
});

const rejectionCode = (receipt: ProjectionReceipt): ProjectionErrorCode => {
  if (receipt.message === "display_topology_ineligible") return "topology-ineligible";
  if (receipt.message === "fullscreen_verification_failed") return "fullscreen-failed";
  if (receipt.message === "generation_mismatch") return "stale-response";
  return "command-rejected";
};

const receiptCorrelates = (
  state: ProjectionSetupState,
  identity: ProjectionIdentity,
  receipt: ProjectionReceipt,
): boolean => {
  const pending = state.pending;
  if (
    pending === undefined ||
    !identityMatches(state.identity, identity) ||
    receipt.commandId !== pending.commandId ||
    receipt.command !== pending.command ||
    receipt.sessionId !== pending.sessionId
  ) {
    return false;
  }
  if (!receipt.accepted) return true;
  const expectedReceiptGeneration =
    pending.command === "assign_projection_window"
      ? pending.expectedGeneration + 1
      : pending.expectedGeneration;
  return receipt.generation === expectedReceiptGeneration;
};

const validAcceptedStatus = (
  command: ProjectionPendingCommand["command"],
  receipt: ProjectionReceipt,
): boolean => {
  switch (command) {
    case "detect_displays":
    case "open_projection_session":
      return receipt.status === "candidate";
    case "assign_projection_window":
      return (
        receipt.status === "assigned" &&
        receipt.assignments.length === 2 &&
        new Set(receipt.assignments.map(({ role }) => role)).size === 2 &&
        new Set(receipt.assignments.map(({ displayId }) => displayId)).size === 2
      );
    case "enter_projection_fullscreen":
      return receipt.status === "fullscreen";
    case "verify_projection_assignment":
      return receipt.status === "witness_pending" || receipt.status === "certified";
    case "close_projection_session":
      return receipt.status === "closed";
  }
};

const acceptReceipt = (
  state: ProjectionSetupState,
  receipt: ProjectionReceipt,
): ProjectionSetupState => {
  const command = state.pending!.command;
  switch (command) {
    case "detect_displays":
      return {
        ...state,
        status: "running",
        pending: undefined,
        swapPending: undefined,
        error: undefined,
        generation: receipt.generation,
        steps: { ...state.steps, detect: "complete" },
      };
    case "open_projection_session":
      return {
        ...state,
        status: "running",
        pending: undefined,
        error: undefined,
        generation: receipt.generation,
      };
    case "assign_projection_window":
      return {
        ...state,
        status: "running",
        pending: undefined,
        error: undefined,
        generation: receipt.generation,
        assignment:
          state.assignment === "external-stage" && state.swapPending === true
            ? "internal-stage"
            : state.assignment === "internal-stage" && state.swapPending === true
              ? "external-stage"
              : state.assignment,
        swapPending: undefined,
        steps: { ...state.steps, assign: "complete" },
      };
    case "enter_projection_fullscreen":
      return {
        ...state,
        status: "witness-ready",
        pending: undefined,
        error: undefined,
        generation: receipt.generation,
        steps: { ...state.steps, fullscreen: "complete", witness: "active" },
      };
    case "verify_projection_assignment": {
      const certified = receipt.status === "certified";
      return {
        ...state,
        status: certified ? "certified" : "witness-pending",
        pending: undefined,
        error: undefined,
        generation: receipt.generation,
        physicalDualScreenCertified: certified,
        steps: { ...state.steps, witness: certified ? "complete" : "active" },
      };
    }
    case "close_projection_session":
      return {
        ...state,
        status: "closed",
        pending: undefined,
        error: undefined,
        generation: receipt.generation,
        physicalDualScreenCertified: false,
      };
  }
};

export const reduceProjectionSetup = (
  state: ProjectionSetupState,
  action: ProjectionSetupAction,
): ProjectionSetupState => {
  switch (action.type) {
    case "COMMAND_STARTED": {
      const swap = action.swap === true;
      return {
        ...state,
        status: "running",
        pending: action.pending,
        swapPending: swap,
        error: undefined,
        physicalDualScreenCertified: false,
        steps: activeStep(
          swap
            ? { ...state.steps, fullscreen: "waiting", witness: "waiting" }
            : state.steps,
          action.pending.command,
        ),
      };
    }
    case "RECEIPT_RECEIVED":
      if (!receiptCorrelates(state, action.identity, action.receipt)) {
        return invalidated(state);
      }
      if (!action.receipt.accepted) {
        const code = rejectionCode(action.receipt);
        return {
          ...invalidated(state, code),
          status: code === "topology-ineligible" ? "error" : "invalidated",
        };
      }
      if (!validAcceptedStatus(state.pending!.command, action.receipt)) {
        return invalidated(state);
      }
      return acceptReceipt(state, action.receipt);
    case "COMMAND_FAILED":
      return {
        ...state,
        status: "error",
        pending: undefined,
        error: action.code,
        physicalDualScreenCertified: false,
      };
    case "WITNESS_INVALIDATED":
      return invalidated(state, "command-rejected");
    case "RESET":
      return initialProjectionSetup(state.identity);
  }
};

export const projectionErrorMessage = (
  error: ProjectionErrorCode | undefined,
): string => {
  switch (error) {
    case "runtime-unavailable":
      return "本机双屏服务未连接";
    case "host-unavailable":
      return "双屏主机暂不可用";
    case "topology-ineligible":
      return "需要本机扩展双屏";
    case "fullscreen-failed":
      return "窗口未能保持全屏";
    case "content-unavailable":
      return "发布课程暂不可投屏";
    case "stale-response":
      return "屏幕状态已变化，请重新确认";
    case "command-rejected":
      return "本次投屏确认未完成";
    case "command-failed":
      return "双屏操作未完成";
    default:
      return "";
  }
};
