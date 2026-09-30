interface ConsoleLike {
  log(...args: unknown[]): void
  error(...args: unknown[]): void
}

interface ProcessLike {
  env: Record<string, string | undefined>
  argv: string[]
  exitCode?: number
  stdin: {
    setEncoding(encoding: string): void
    [Symbol.asyncIterator](): AsyncIterator<string>
  }
  stdout: { write(chunk: string): void }
}

declare const console: ConsoleLike
declare const process: ProcessLike

interface SocketEvent {
  data?: unknown
  code?: number
}

interface AdaptiveWebSocket {
  addEventListener(type: "open" | "message" | "close" | "error", listener: (event: SocketEvent) => void): void
  close(code?: number, reason?: string): void
}

interface AdaptiveWebSocketConstructor {
  new (url: string, options: { headers?: Record<string, string> }): AdaptiveWebSocket
  new (url: string, protocols?: string | string[]): AdaptiveWebSocket
}

declare const WebSocket: AdaptiveWebSocketConstructor

interface FetchResponse {
  status: number
  text(): Promise<string>
}

interface FetchInit {
  method?: string
  headers?: Record<string, string>
  body?: string
  redirect?: "error" | "follow" | "manual"
}

declare function fetch(input: string, init?: FetchInit): Promise<FetchResponse>

declare function queueMicrotask(callback: () => void): void
declare function setTimeout(handler: () => void, timeout?: number): number
declare function clearTimeout(id: number): void

interface URL {
  protocol: string
  pathname: string
  search: string
  hash: string
  username: string
  password: string
  toString(): string
}

declare const URL: {
  new (input: string, base?: string): URL
}

interface URLSearchParams {
  toString(): string
}

declare const URLSearchParams: {
  new (init?: Record<string, string>): URLSearchParams
}

interface ImportMeta {
  readonly url: string
}
