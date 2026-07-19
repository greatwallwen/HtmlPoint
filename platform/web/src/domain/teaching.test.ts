import { describe, expect, it, vi } from "vitest";

import {
  createTeachingBus,
  initialTeachingSetup,
  reduceTeachingSetup,
  type TeachingBusEnvelope,
  type TeachingBusHarness,
  type TeachingChannelListener,
  type TeachingFrame,
  type TeachingMessageChannel,
  type TeachingSetupAction,
  type TeachingSetupState,
  type TeachingStorage,
} from "./teaching";

const SESSION_ID = "session-teaching";

const reduceAll = (
  state: TeachingSetupState,
  ...actions: TeachingSetupAction[]
) => actions.reduce(reduceTeachingSetup, state);

const advanceToOpening = (rehearsalMode: boolean) => {
  const permissionRequired = reduceAll(
    initialTeachingSetup(SESSION_ID),
    { type: "CHECK_STARTED" },
    { type: "CAPABILITY_RESOLVED", screenDetails: !rehearsalMode },
  );

  return reduceTeachingSetup(
    permissionRequired,
    rehearsalMode
      ? { type: "REHEARSAL_ACCEPTED" }
      : { type: "PERMISSION_GRANTED" },
  );
};

const advanceToReady = (rehearsalMode: boolean) =>
  reduceAll(
    advanceToOpening(rehearsalMode),
    { type: "WINDOWS_OPENED", stage: true, presenter: true },
    { type: "ROLE_CONNECTED", role: "stage" },
    { type: "ROLE_CONNECTED", role: "presenter" },
    { type: "FRAME_PUBLISHED", sequence: 0 },
    { type: "FRAME_ACKNOWLEDGED", role: "stage", sequence: 0 },
    { type: "FRAME_ACKNOWLEDGED", role: "presenter", sequence: 0 },
    { type: "SYNC_CONFIRMED" },
  );

describe("teaching setup state machine", () => {
  it("requires both windows, both presences, and explicit sync before rehearsal is ready", () => {
    const initial = initialTeachingSetup(SESSION_ID);
    expect(initial).toEqual({
      status: "idle",
      sessionId: SESSION_ID,
      rehearsalMode: false,
      stageOpen: false,
      presenterOpen: false,
      stageConnected: false,
      presenterConnected: false,
      stageFrameAcknowledged: false,
      presenterFrameAcknowledged: false,
      syncConfirmed: false,
      physicalDualScreenCertified: false,
    });

    const checking = reduceTeachingSetup(initial, { type: "CHECK_STARTED" });
    const permissionRequired = reduceTeachingSetup(checking, {
      type: "CAPABILITY_RESOLVED",
      screenDetails: false,
    });
    const opening = reduceTeachingSetup(permissionRequired, {
      type: "REHEARSAL_ACCEPTED",
    });

    expect(permissionRequired).toMatchObject({
      status: "permission-required",
      screenDetailsAvailable: false,
      physicalDualScreenCertified: false,
    });
    expect(opening).toMatchObject({
      status: "opening",
      rehearsalMode: true,
      physicalDualScreenCertified: false,
    });

    const earlyPresence = reduceTeachingSetup(opening, {
      type: "ROLE_CONNECTED",
      role: "stage",
    });
    expect(earlyPresence).toBe(opening);

    const syncing = reduceTeachingSetup(opening, {
      type: "WINDOWS_OPENED",
      stage: true,
      presenter: true,
    });
    const stagePresent = reduceTeachingSetup(syncing, {
      type: "ROLE_CONNECTED",
      role: "stage",
    });
    const prematureSync = reduceTeachingSetup(stagePresent, {
      type: "SYNC_CONFIRMED",
    });

    expect(syncing.status).toBe("syncing");
    expect(stagePresent.status).toBe("syncing");
    expect(prematureSync).toBe(stagePresent);

    const bothPresent = reduceTeachingSetup(stagePresent, {
      type: "ROLE_CONNECTED",
      role: "presenter",
    });
    const presenceOnly = reduceTeachingSetup(bothPresent, {
      type: "SYNC_CONFIRMED",
    });
    expect(presenceOnly).toBe(bothPresent);

    const framePublished = reduceTeachingSetup(bothPresent, {
      type: "FRAME_PUBLISHED",
      sequence: 7,
    });
    const stageAcknowledged = reduceTeachingSetup(framePublished, {
      type: "FRAME_ACKNOWLEDGED",
      role: "stage",
      sequence: 7,
    });
    expect(
      reduceTeachingSetup(stageAcknowledged, { type: "SYNC_CONFIRMED" }),
    ).toBe(stageAcknowledged);
    const bothAcknowledged = reduceTeachingSetup(stageAcknowledged, {
      type: "FRAME_ACKNOWLEDGED",
      role: "presenter",
      sequence: 7,
    });
    const ready = reduceTeachingSetup(bothAcknowledged, {
      type: "SYNC_CONFIRMED",
    });

    expect(ready).toMatchObject({
      status: "ready",
      rehearsalMode: true,
      stageOpen: true,
      presenterOpen: true,
      stageConnected: true,
      presenterConnected: true,
      stageFrameAcknowledged: true,
      presenterFrameAcknowledged: true,
      syncConfirmed: true,
      physicalDualScreenCertified: false,
    });
  });

  it("retains the session through permission denial and recovers by retry or rehearsal", () => {
    const permissionRequired = reduceAll(
      initialTeachingSetup(SESSION_ID),
      { type: "CHECK_STARTED" },
      { type: "CAPABILITY_RESOLVED", screenDetails: true },
    );
    const denied = reduceTeachingSetup(permissionRequired, {
      type: "PERMISSION_DENIED",
    });

    expect(denied).toMatchObject({
      status: "error",
      sessionId: SESSION_ID,
      physicalDualScreenCertified: false,
      error: { code: "permission-denied" },
    });
    expect(denied.error?.message).toMatch(/[\u3400-\u9fff]/);

    const retried = reduceTeachingSetup(denied, { type: "CHECK_STARTED" });
    expect(retried).toMatchObject({ status: "checking", sessionId: SESSION_ID });
    expect(retried.error).toBeUndefined();

    const rehearsal = reduceTeachingSetup(denied, {
      type: "REHEARSAL_ACCEPTED",
    });
    expect(rehearsal).toMatchObject({
      status: "opening",
      sessionId: SESSION_ID,
      rehearsalMode: true,
      physicalDualScreenCertified: false,
    });
    expect(rehearsal.error).toBeUndefined();
  });

  it("retains opened windows through a popup-blocked error and permits retry", () => {
    const blocked = reduceTeachingSetup(advanceToOpening(false), {
      type: "WINDOWS_OPENED",
      stage: true,
      presenter: false,
    });

    expect(blocked).toMatchObject({
      status: "error",
      sessionId: SESSION_ID,
      stageOpen: true,
      presenterOpen: false,
      physicalDualScreenCertified: false,
      error: { code: "popup-blocked" },
    });
    expect(blocked.error?.message).toMatch(/[\u3400-\u9fff]/);

    const retried = reduceTeachingSetup(blocked, { type: "CHECK_STARTED" });
    expect(retried).toMatchObject({
      status: "checking",
      sessionId: SESSION_ID,
      stageOpen: true,
      presenterOpen: false,
    });
    expect(retried.error).toBeUndefined();
  });

  it("returns the same object for illegal and already-satisfied transitions", () => {
    const idle = initialTeachingSetup(SESSION_ID);
    expect(
      reduceTeachingSetup(idle, { type: "PERMISSION_GRANTED" }),
    ).toBe(idle);
    expect(
      reduceTeachingSetup(idle, {
        type: "CAPABILITY_RESOLVED",
        screenDetails: true,
      }),
    ).toBe(idle);
    expect(reduceTeachingSetup(idle, { type: "SYNC_CONFIRMED" })).toBe(idle);
    expect(reduceTeachingSetup(idle, { type: "START_PRESENTING" })).toBe(idle);

    const checking = reduceTeachingSetup(idle, { type: "CHECK_STARTED" });
    expect(reduceTeachingSetup(checking, { type: "CHECK_STARTED" })).toBe(
      checking,
    );

    const ready = advanceToReady(false);
    expect(
      reduceTeachingSetup(ready, { type: "ROLE_CONNECTED", role: "stage" }),
    ).toBe(ready);
    expect(reduceTeachingSetup(ready, { type: "STOP_PRESENTING" })).toBe(ready);
  });

  it("supports present, stop, connection loss, and explicit window recovery", () => {
    const ready = advanceToReady(false);
    expect(ready.physicalDualScreenCertified).toBe(false);

    const presenting = reduceTeachingSetup(ready, { type: "START_PRESENTING" });
    expect(presenting.status).toBe("presenting");

    const stopped = reduceTeachingSetup(presenting, { type: "STOP_PRESENTING" });
    expect(stopped.status).toBe("ready");

    const presentingAgain = reduceTeachingSetup(stopped, {
      type: "START_PRESENTING",
    });
    const disconnected = reduceTeachingSetup(presentingAgain, {
      type: "ROLE_DISCONNECTED",
      role: "stage",
    });
    expect(disconnected).toMatchObject({
      status: "error",
      stageOpen: false,
      presenterOpen: true,
      stageConnected: false,
      presenterConnected: true,
      stageFrameAcknowledged: false,
      presenterFrameAcknowledged: false,
      syncConfirmed: false,
      physicalDualScreenCertified: false,
      error: { code: "sync-failed" },
    });
    expect(
      reduceTeachingSetup(disconnected, { type: "SYNC_CONFIRMED" }),
    ).toBe(disconnected);

    const reopening = reduceTeachingSetup(disconnected, {
      type: "REOPEN_WINDOWS_REQUESTED",
    });
    expect(reopening.status).toBe("opening");
    const reconnected = reduceAll(
      reopening,
      { type: "WINDOWS_OPENED", stage: true, presenter: true },
      { type: "ROLE_CONNECTED", role: "stage" },
      { type: "ROLE_CONNECTED", role: "presenter" },
      { type: "FRAME_PUBLISHED", sequence: 0 },
      { type: "FRAME_ACKNOWLEDGED", role: "stage", sequence: 0 },
      { type: "FRAME_ACKNOWLEDGED", role: "presenter", sequence: 0 },
    );
    const resynced = reduceTeachingSetup(reconnected, {
      type: "SYNC_CONFIRMED",
    });
    expect(resynced).toMatchObject({
      status: "ready",
      stageConnected: true,
      presenterConnected: true,
      stageFrameAcknowledged: true,
      presenterFrameAcknowledged: true,
      syncConfirmed: true,
      physicalDualScreenCertified: false,
    });

    const ended = reduceTeachingSetup(resynced, { type: "SESSION_ENDED" });
    expect(ended).toEqual(initialTeachingSetup(SESSION_ID));
    expect(
      reduceTeachingSetup(initialTeachingSetup(SESSION_ID), {
        type: "REOPEN_WINDOWS_REQUESTED",
      }),
    ).toEqual(initialTeachingSetup(SESSION_ID));
  });

  it("keeps rehearsal uncertified and guards the pure physical assignment transition", () => {
    const rehearsalReady = advanceToReady(true);
    const rehearsalAttempt = reduceTeachingSetup(rehearsalReady, {
      type: "PHYSICAL_ASSIGNMENT_CONFIRMED",
    });
    expect(rehearsalAttempt).toBe(rehearsalReady);
    expect(rehearsalAttempt.physicalDualScreenCertified).toBe(false);

    const opening = advanceToOpening(false);
    const outOfOrder = reduceTeachingSetup(opening, {
      type: "PHYSICAL_ASSIGNMENT_CONFIRMED",
    });
    expect(outOfOrder).toBe(opening);

    const syncing = reduceAll(
      opening,
      { type: "WINDOWS_OPENED", stage: true, presenter: true },
      { type: "ROLE_CONNECTED", role: "stage" },
      { type: "ROLE_CONNECTED", role: "presenter" },
    );
    const beforeSync = reduceTeachingSetup(syncing, {
      type: "PHYSICAL_ASSIGNMENT_CONFIRMED",
    });
    expect(beforeSync).toBe(syncing);
    expect(beforeSync.physicalDualScreenCertified).toBe(false);

    const acknowledged = reduceAll(
      syncing,
      { type: "FRAME_PUBLISHED", sequence: 0 },
      { type: "FRAME_ACKNOWLEDGED", role: "stage", sequence: 0 },
      { type: "FRAME_ACKNOWLEDGED", role: "presenter", sequence: 0 },
    );
    const ready = reduceTeachingSetup(acknowledged, {
      type: "SYNC_CONFIRMED",
    });
    expect(ready.physicalDualScreenCertified).toBe(false);

    // This verifies only the reducer guard after an explicit external confirmation.
    // It is not evidence that the current environment has dual-screen hardware.
    const guardAccepted = reduceTeachingSetup(ready, {
      type: "PHYSICAL_ASSIGNMENT_CONFIRMED",
    });
    expect(guardAccepted.physicalDualScreenCertified).toBe(true);

    const connectionLost = reduceTeachingSetup(guardAccepted, {
      type: "ROLE_DISCONNECTED",
      role: "presenter",
    });
    expect(connectionLost.physicalDualScreenCertified).toBe(false);
  });
});

type Operation = `storage:${string}` | `broadcast:${string}`;

class MemoryStorage implements TeachingStorage {
  readonly values = new Map<string, string>();

  constructor(private readonly operations: Operation[]) {}

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.operations.push(`storage:${key}`);
    this.values.set(key, value);
  }
}

class MemoryChannel implements TeachingMessageChannel {
  private readonly listeners = new Set<TeachingChannelListener>();
  closed = false;

  constructor(
    readonly name: string,
    private readonly network: MemoryChannelNetwork,
  ) {}

  postMessage(message: unknown) {
    if (!this.closed) this.network.broadcast(this, message);
  }

  addEventListener(type: "message", listener: TeachingChannelListener) {
    if (type === "message" && !this.closed) this.listeners.add(listener);
  }

  removeEventListener(type: "message", listener: TeachingChannelListener) {
    if (type === "message" && this.listeners.delete(listener)) {
      this.network.recordRemovedHandler();
    }
  }

  receive(message: unknown) {
    if (this.closed) return;
    for (const listener of this.listeners) listener({ data: message });
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.listeners.clear();
    this.network.close(this);
  }
}

class MemoryChannelNetwork {
  readonly broadcasts: Array<{ channel: string; message: unknown }> = [];
  readonly closedChannels: string[] = [];
  readonly openedChannels: string[] = [];
  removedHandlerCount = 0;
  private readonly channels = new Map<string, Set<MemoryChannel>>();

  constructor(private readonly operations: Operation[]) {}

  createChannel(name: string) {
    const channel = new MemoryChannel(name, this);
    const peers = this.channels.get(name) ?? new Set<MemoryChannel>();
    peers.add(channel);
    this.channels.set(name, peers);
    this.openedChannels.push(name);
    return channel;
  }

  broadcast(sender: MemoryChannel, message: unknown) {
    this.operations.push(`broadcast:${sender.name}`);
    this.broadcasts.push({ channel: sender.name, message });
    for (const channel of this.channels.get(sender.name) ?? []) {
      if (channel !== sender) channel.receive(message);
    }
  }

  inject(name: string, message: unknown) {
    for (const channel of this.channels.get(name) ?? []) channel.receive(message);
  }

  close(channel: MemoryChannel) {
    this.closedChannels.push(channel.name);
    this.channels.get(channel.name)?.delete(channel);
  }

  recordRemovedHandler() {
    this.removedHandlerCount += 1;
  }
}

class FakeNativeBroadcastChannel {
  private static readonly channels = new Map<
    string,
    Set<FakeNativeBroadcastChannel>
  >();

  private readonly listeners = new Set<EventListenerOrEventListenerObject>();
  private closed = false;

  constructor(readonly name: string) {
    const peers = FakeNativeBroadcastChannel.channels.get(name) ?? new Set();
    peers.add(this);
    FakeNativeBroadcastChannel.channels.set(name, peers);
  }

  postMessage(message: unknown) {
    if (this.closed) return;
    for (const peer of FakeNativeBroadcastChannel.channels.get(this.name) ?? []) {
      if (peer !== this) peer.receive(structuredClone(message));
    }
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    if (type === "message" && !this.closed) this.listeners.add(listener);
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    if (type === "message") this.listeners.delete(listener);
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.listeners.clear();
    FakeNativeBroadcastChannel.channels.get(this.name)?.delete(this);
  }

  private receive(data: unknown) {
    if (this.closed) return;
    const event = new MessageEvent("message", { data });
    for (const listener of this.listeners) {
      if (typeof listener === "function") listener.call(this, event);
      else listener.handleEvent(event);
    }
  }

  static reset() {
    FakeNativeBroadcastChannel.channels.clear();
  }
}

const makeHarness = () => {
  const operations: Operation[] = [];
  const storage = new MemoryStorage(operations);
  const network = new MemoryChannelNetwork(operations);
  let timestampIndex = 0;
  const harness: TeachingBusHarness = {
    storage,
    createChannel: (name) => network.createChannel(name),
    now: () =>
      `2026-07-15T00:00:${String(timestampIndex++).padStart(2, "0")}.000Z`,
  };
  return { harness, network, operations, storage };
};

const makeFrame = (overrides: Partial<TeachingFrame> = {}): TeachingFrame => ({
  sessionId: "session-bus",
  courseId: "course-1",
  lessonId: "lesson-1",
  lessonIndex: 0,
  lessonCount: 3,
  playing: false,
  elapsedSeconds: 12,
  sequence: 1,
  sentAt: "2026-07-15T00:00:00.000Z",
  ...overrides,
});

describe("teaching session bus", () => {
  it("stores a valid frame before broadcasting it exactly once", () => {
    const { harness, network, operations, storage } = makeHarness();
    const bus = createTeachingBus("session-bus", harness);
    const frame = makeFrame();

    bus.publish(frame);

    const recoveryKey = "course-teaching:last-frame:session-bus";
    const channelName = "course-teaching:session-bus";
    expect(JSON.parse(storage.getItem(recoveryKey) ?? "null")).toEqual(frame);
    expect(network.openedChannels).toEqual([channelName]);
    expect(network.broadcasts).toEqual([
      {
        channel: channelName,
        message: {
          type: "frame",
          sessionId: "session-bus",
          sentAt: frame.sentAt,
          frame,
        },
      },
    ]);
    expect(operations).toEqual([
      `storage:${recoveryKey}`,
      `broadcast:${channelName}`,
    ]);
  });

  it("still broadcasts a newest frame when recovery storage rejects the write", () => {
    const { harness, network } = makeHarness();
    const rejectingHarness: TeachingBusHarness = {
      ...harness,
      storage: {
        getItem: () => null,
        setItem: () => {
          throw new Error("quota exceeded");
        },
      },
    };
    const bus = createTeachingBus("session-bus", rejectingHarness);

    expect(() => bus.publish(makeFrame())).not.toThrow();
    expect(network.broadcasts).toHaveLength(1);
    expect(network.broadcasts[0]?.message).toMatchObject({
      type: "frame",
      frame: { sequence: 1 },
    });
  });

  it("lets a new bus recover the newest valid stored frame", () => {
    const { harness } = makeHarness();
    const first = createTeachingBus("session-bus", harness);
    const newer = makeFrame({ sequence: 4, elapsedSeconds: 40 });
    const older = makeFrame({ sequence: 3, elapsedSeconds: 30 });

    first.publish(newer);
    first.publish(older);

    const reconnected = createTeachingBus("session-bus", harness);
    expect(reconnected.readLastFrame()).toEqual(newer);
  });

  it("delivers frame, acknowledgement, presence, and heartbeat envelopes to a peer in order", () => {
    const { harness } = makeHarness();
    const first = createTeachingBus("session-bus", harness);
    const second = createTeachingBus("session-bus", harness);
    const received: TeachingBusEnvelope[] = [];
    second.subscribe((envelope) => received.push(envelope));

    first.publish(makeFrame());
    first.acknowledge("stage", 1);
    first.announce("stage");
    first.heartbeat("presenter");

    expect(received.map((envelope) => envelope.type)).toEqual([
      "frame",
      "frame-ack",
      "presence",
      "heartbeat",
    ]);
    expect(received[0]).toMatchObject({
      type: "frame",
      sessionId: "session-bus",
      frame: { sequence: 1 },
    });
    expect(received[1]).toEqual({
      type: "frame-ack",
      sessionId: "session-bus",
      role: "stage",
      sequence: 1,
      sentAt: "2026-07-15T00:00:00.000Z",
    });
    expect(received[2]).toEqual({
      type: "presence",
      sessionId: "session-bus",
      role: "stage",
      sentAt: "2026-07-15T00:00:01.000Z",
    });
    expect(received[3]).toEqual({
      type: "heartbeat",
      sessionId: "session-bus",
      role: "presenter",
      sentAt: "2026-07-15T00:00:02.000Z",
    });
  });

  it("unsubscribe and close prevent later delivery and close the channel once", () => {
    const { harness, network } = makeHarness();
    const sender = createTeachingBus("session-bus", harness);
    const receiver = createTeachingBus("session-bus", harness);
    const received: TeachingBusEnvelope[] = [];
    const unsubscribe = receiver.subscribe((envelope) => received.push(envelope));

    sender.publish(makeFrame());
    expect(received).toHaveLength(1);

    unsubscribe();
    sender.announce("stage");
    expect(received).toHaveLength(1);

    receiver.subscribe((envelope) => received.push(envelope));
    receiver.close();
    receiver.close();
    sender.heartbeat("stage");
    expect(received).toHaveLength(1);
    expect(network.closedChannels).toEqual(["course-teaching:session-bus"]);
    expect(network.removedHandlerCount).toBe(1);

    const broadcastsBeforeClosedCalls = network.broadcasts.length;
    expect(() => {
      receiver.publish(makeFrame({ sequence: 2 }));
      receiver.announce("presenter");
      receiver.heartbeat("presenter");
    }).not.toThrow();
    expect(network.broadcasts).toHaveLength(broadcastsBeforeClosedCalls);
  });

  it("ignores malformed and wrong-session storage, envelopes, and publishes", () => {
    const { harness, network, storage } = makeHarness();
    const recoveryKey = "course-teaching:last-frame:session-bus";
    const channelName = "course-teaching:session-bus";
    const bus = createTeachingBus("session-bus", harness);
    const received: TeachingBusEnvelope[] = [];
    bus.subscribe((envelope) => received.push(envelope));

    storage.values.set(recoveryKey, "{not-json");
    expect(bus.readLastFrame()).toBeUndefined();

    storage.values.set(
      recoveryKey,
      JSON.stringify(makeFrame({ sessionId: "another-session" })),
    );
    expect(bus.readLastFrame()).toBeUndefined();

    storage.values.set(
      recoveryKey,
      JSON.stringify(makeFrame({ sentAt: "not-a-timestamp" })),
    );
    expect(bus.readLastFrame()).toBeUndefined();

    expect(() => {
      network.inject(channelName, null);
      network.inject(channelName, "not-an-envelope");
      network.inject(channelName, {
        type: "presence",
        sessionId: "another-session",
        role: "stage",
        sentAt: "2026-07-15T00:00:00.000Z",
      });
      network.inject(channelName, {
        type: "frame",
        sessionId: "session-bus",
        sentAt: "2026-07-15T00:00:00.000Z",
        frame: makeFrame({ lessonCount: 0 }),
      });
      network.inject(channelName, {
        type: "heartbeat",
        sessionId: "session-bus",
        role: "audience",
        sentAt: "not-a-timestamp",
      });
    }).not.toThrow();
    expect(received).toEqual([]);

    const broadcastCount = network.broadcasts.length;
    bus.publish(makeFrame({ sessionId: "another-session" }));
    bus.publish(makeFrame({ lessonIndex: -1 }));
    expect(network.broadcasts).toHaveLength(broadcastCount);

    network.inject(channelName, {
      type: "presence",
      sessionId: "session-bus",
      role: "controller",
      sentAt: "2026-07-15T00:00:00.000Z",
    });
    expect(received).toHaveLength(1);

    bus.close();
    network.inject(channelName, {
      type: "presence",
      sessionId: "session-bus",
      role: "stage",
      sentAt: "2026-07-15T00:00:01.000Z",
    });
    expect(received).toHaveLength(1);
    expect(bus.readLastFrame()).toBeUndefined();
  });

  it("uses the native BroadcastChannel-compatible adapter by default", () => {
    const recoveryKey = "course-teaching:last-frame:session-bus";
    let sender: ReturnType<typeof createTeachingBus> | undefined;
    let receiver: ReturnType<typeof createTeachingBus> | undefined;

    FakeNativeBroadcastChannel.reset();
    localStorage.removeItem(recoveryKey);
    vi.stubGlobal("BroadcastChannel", FakeNativeBroadcastChannel);

    try {
      sender = createTeachingBus("session-bus");
      receiver = createTeachingBus("session-bus");
      const received: TeachingBusEnvelope[] = [];
      receiver.subscribe((envelope) => received.push(envelope));

      sender.publish(makeFrame());

      expect(received).toHaveLength(1);
      expect(received[0]).toMatchObject({
        type: "frame",
        sessionId: "session-bus",
        frame: { sequence: 1 },
      });
      expect(JSON.parse(localStorage.getItem(recoveryKey) ?? "null")).toEqual(
        makeFrame(),
      );
    } finally {
      sender?.close();
      receiver?.close();
      localStorage.removeItem(recoveryKey);
      FakeNativeBroadcastChannel.reset();
      vi.unstubAllGlobals();
    }
  });
});
