import { useSyncExternalStore } from "react";

import { streamChat, type AgentEvent } from "../api/client";

/**
 * The conversation, kept outside React's tree.
 *
 * `/chat` is a route, so opening `/postings` unmounts it — and with the
 * transcript in `useState`, going to look at a posting the agent just found
 * threw the conversation away. Which is the one thing you would want to do
 * with a list of postings.
 *
 * The agent loop lives here too, not just the data. A turn started before you
 * navigated away keeps streaming into this store and is simply there when you
 * come back, rather than being tied to the lifetime of a component nobody is
 * looking at.
 *
 * Deliberately not persisted to disk. This survives navigation, not a reload:
 * the history it carries is the model's, and quietly reviving a week-old
 * conversation would be worse than starting a new one.
 */

export interface Turn {
  question: string;
  events: AgentEvent[];
}

export interface ChatSession {
  turns: Turn[];
  /** The model's own message history, fed back so "mark the first three" resolves. */
  history: unknown[];
  running: boolean;
  /** Kept here so a half-typed question survives navigation too. */
  input: string;
}

const EMPTY: ChatSession = { turns: [], history: [], running: false, input: "" };

let state: ChatSession = EMPTY;
let controller: AbortController | null = null;
const listeners = new Set<() => void>();

function publish(next: Partial<ChatSession>): void {
  state = { ...state, ...next };
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Stable between changes, which is what useSyncExternalStore requires. */
function snapshot(): ChatSession {
  return state;
}

export function useChatSession(): ChatSession {
  return useSyncExternalStore(subscribe, snapshot);
}

export function setInput(input: string): void {
  publish({ input });
}

function appendEvent(index: number, event: AgentEvent): void {
  publish({
    turns: state.turns.map((turn, i) =>
      i === index ? { ...turn, events: [...turn.events, event] } : turn,
    ),
  });
}

export async function send(message: string): Promise<void> {
  if (!message.trim() || state.running) return;

  const index = state.turns.length;
  publish({
    input: "",
    running: true,
    turns: [...state.turns, { question: message, events: [] }],
  });

  controller = new AbortController();
  const signal = controller.signal;

  try {
    for await (const event of streamChat(message, state.history, signal)) {
      // Appending per event is what makes tool calls arrive one at a time
      // rather than all at once when the turn finishes.
      appendEvent(index, event);
      if (event.kind === "done" && Array.isArray(event.history)) {
        publish({ history: event.history });
      }
    }
  } catch (error) {
    if (!signal.aborted) {
      appendEvent(index, {
        kind: "error",
        detail: error instanceof Error ? error.message : String(error),
        status: 0,
      });
    }
  } finally {
    publish({ running: false });
    controller = null;
  }
}

export function stop(): void {
  controller?.abort();
}

/** Start over. The model's history goes with it, or the next turn would refer
 *  to postings that are no longer on screen. */
export function reset(): void {
  controller?.abort();
  controller = null;
  state = EMPTY;
  for (const listener of listeners) listener();
}
