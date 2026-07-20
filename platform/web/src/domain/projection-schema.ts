import { z } from "zod";

export const projectionCommandNameSchema = z.enum([
  "detect_displays",
  "open_projection_session",
  "assign_projection_window",
  "enter_projection_fullscreen",
  "verify_projection_assignment",
  "close_projection_session",
]);

export const projectionStatusSchema = z.enum([
  "undetected",
  "candidate",
  "assigned",
  "fullscreen",
  "syncing",
  "witness_pending",
  "certified",
  "invalidated",
  "closed",
]);

export const projectionEventTypeSchema = z.enum([
  "topology_detected",
  "session_opened",
  "window_assigned",
  "fullscreen_entered",
  "frame_committed",
  "assignment_verified",
  "witness_started",
  "witness_confirmed",
  "session_certified",
  "session_invalidated",
  "session_closed",
  "host_error",
]);

const boundedPayloadSchema = z
  .record(z.string(), z.json())
  .refine((value) => Object.keys(value).length <= 32, "payload is too large");

export const projectionCommandSchema = z
  .object({
    schemaVersion: z.literal(1),
    commandId: z.string().uuid(),
    command: projectionCommandNameSchema,
    sessionId: z.string().uuid().nullable(),
    expectedGeneration: z.number().int().min(0).max(2_147_483_647),
    payload: boundedPayloadSchema,
  })
  .strict();

export const projectionRectangleSchema = z
  .object({
    x: z.number().int().min(-1_000_000).max(1_000_000),
    y: z.number().int().min(-1_000_000).max(1_000_000),
    width: z.number().int().min(1).max(100_000),
    height: z.number().int().min(1).max(100_000),
  })
  .strict();

const digestSchema = z.string().regex(/^[0-9a-f]{64}$/);

export const projectionDisplaySchema = z
  .object({
    displayId: digestSchema,
    bounds: projectionRectangleSchema,
    workArea: projectionRectangleSchema,
    isPrimary: z.boolean(),
    isInternal: z.boolean(),
    scalePercent: z.number().int().min(50).max(500),
    refreshRateMilliHertz: z.number().int().min(1_000).max(1_000_000),
  })
  .strict();

export const displayTopologySchema = z
  .object({
    schemaVersion: z.literal(1),
    topologyId: digestSchema,
    capturedAt: z.string().datetime({ offset: true }),
    sessionKind: z.enum(["interactive_local", "remote", "unknown"]),
    mode: z.enum(["single", "extended", "duplicate", "unknown"]),
    displays: z.array(projectionDisplaySchema).min(1).max(16),
  })
  .strict()
  .superRefine(({ displays }, context) => {
    const ids = displays.map(({ displayId }) => displayId);
    if (new Set(ids).size !== ids.length) {
      context.addIssue({ code: "custom", message: "displayId values must be unique" });
    }
    if (displays.filter(({ isPrimary }) => isPrimary).length !== 1) {
      context.addIssue({ code: "custom", message: "exactly one display must be primary" });
    }
  });

export const projectionAssignmentSchema = z
  .object({
    role: z.enum(["stage", "presenter"]),
    displayId: digestSchema,
    windowGeneration: z.number().int().min(0).max(2_147_483_647),
  })
  .strict();

export const projectionReceiptSchema = z
  .object({
    schemaVersion: z.literal(1),
    commandId: z.string().uuid(),
    sessionId: z.string().uuid().nullable(),
    command: projectionCommandNameSchema,
    accepted: z.boolean(),
    status: projectionStatusSchema,
    generation: z.number().int().min(0).max(2_147_483_647),
    message: z.string().max(500),
    assignments: z.array(projectionAssignmentSchema).max(2),
  })
  .strict()
  .superRefine(({ assignments }, context) => {
    const roles = assignments.map(({ role }) => role);
    const displays = assignments.map(({ displayId }) => displayId);
    if (new Set(roles).size !== roles.length) {
      context.addIssue({ code: "custom", message: "roles must be unique" });
    }
    if (new Set(displays).size !== displays.length) {
      context.addIssue({ code: "custom", message: "displayId values must be unique" });
    }
  });

export const projectionEventSchema = z
  .object({
    schemaVersion: z.literal(1),
    eventId: z.string().uuid(),
    sessionId: z.string().uuid(),
    generation: z.number().int().min(0).max(2_147_483_647),
    sequence: z.number().int().min(0).max(2_147_483_647),
    occurredAt: z.string().datetime({ offset: true }),
    eventType: projectionEventTypeSchema,
    status: projectionStatusSchema,
    payload: boundedPayloadSchema,
  })
  .strict();

export type ProjectionCommand = z.infer<typeof projectionCommandSchema>;
export type ProjectionReceipt = z.infer<typeof projectionReceiptSchema>;
export type DisplayTopology = z.infer<typeof displayTopologySchema>;
export type ProjectionEvent = z.infer<typeof projectionEventSchema>;
export type ProjectionStatus = z.infer<typeof projectionStatusSchema>;
