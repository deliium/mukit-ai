import { readFileSync, readdirSync, statSync } from "node:fs"
import { join } from "node:path"

import type { HttpInit, HttpResponse } from "../src/client.js"

export function fixture(name: string): Record<string, unknown> {
  const file = new URL(`../../../fixtures/${name}`, import.meta.url)
  return JSON.parse(readFileSync(decodeURIComponent(file.pathname), "utf8")) as Record<string, unknown>
}

export function sessionDocument(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...fixture("session.v1.json"), ...changes }
}

export function commandDocument(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...fixture("command-result.v1.json"), ...changes }
}

export function errorBody(
  code: string,
  message: string,
  extra: { session_id?: string | null; retry_after_ms?: number | null; details?: Record<string, unknown> | null } = {},
): Record<string, unknown> {
  return {
    detail: {
      schema_version: "adaptive.engine.error.v1",
      code,
      message,
      session_id: extra.session_id ?? null,
      retry_after_ms: extra.retry_after_ms ?? null,
      details: extra.details ?? null,
    },
  }
}

export class RecordingFetch {
  calls: Array<{ method: string; url: string; headers: Record<string, string>; body?: string }> = []
  private queued: HttpResponse[] = []

  push(status: number, body: unknown): void {
    const text = typeof body === "string" ? body : JSON.stringify(body)
    this.queued.push({ status, body: text })
  }

  readonly fetch = async (url: string, init: HttpInit): Promise<HttpResponse> => {
    this.calls.push({ method: init.method, url, headers: { ...init.headers }, body: init.body })
    const next = this.queued.shift()
    if (!next) throw new Error(`unexpected ${init.method} ${url}`)
    return next
  }
}

export class FakeSocket {
  private listeners = new Map<string, Array<(event: { data?: unknown; code?: number }) => void>>()

  constructor(
    readonly url: string,
    readonly headers: Record<string, string>,
  ) {}

  addEventListener(type: string, listener: (event: { data?: unknown; code?: number }) => void): void {
    const list = this.listeners.get(type) ?? []
    list.push(listener)
    this.listeners.set(type, list)
  }

  emit(type: "open" | "message" | "close" | "error", event: { data?: unknown; code?: number } = {}): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }

  close(): void {
    this.emit("close", { code: 1006 })
  }
}

export class ScriptedOpener {
  readonly sockets: FakeSocket[] = []

  readonly open = (url: string, headers: Record<string, string>): FakeSocket => {
    const socket = new FakeSocket(url, headers)
    this.sockets.push(socket)
    queueMicrotask(() => socket.emit("open"))
    return socket
  }
}

export function captureLogs(): { text: () => string; restore: () => void } {
  const lines: string[] = []
  const previousLog = console.log
  const previousError = console.error
  console.log = (...args: unknown[]) => {
    lines.push(args.map(String).join(" "))
  }
  console.error = (...args: unknown[]) => {
    lines.push(args.map(String).join(" "))
  }
  return {
    text: () => lines.join("\n"),
    restore: () => {
      console.log = previousLog
      console.error = previousError
    },
  }
}

export async function waitFor(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (predicate()) return
    await new Promise((resolve) => setTimeout(() => resolve(undefined), 0))
  }
  throw new Error("timed out")
}

export function assertPackageHasNoStudioImport(): void {
  const root = decodeURIComponent(new URL("../../src/", import.meta.url).pathname)
  const patterns = [/(^|[^\w])import app\b/, /(^|[^\w])from app\b/, /backend\.app/, /frontend\/src/, /frontend\/src/]
  for (const file of walk(root)) {
    if (!file.endsWith(".ts")) continue
    const text = readFileSync(file, "utf8")
    for (const pattern of patterns) {
      if (pattern.test(text)) throw new Error(`${file} matches ${pattern}`)
    }
  }
}

function walk(directory: string): string[] {
  const found: string[] = []
  for (const name of readdirSync(directory)) {
    const path = join(directory, name)
    if (statSync(path).isDirectory()) found.push(...walk(path))
    else found.push(path)
  }
  return found
}
