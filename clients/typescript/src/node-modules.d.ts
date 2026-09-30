declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string
  export function readdirSync(path: string): string[]
  export function statSync(path: string): { isDirectory(): boolean }
  export function existsSync(path: string): boolean
}

declare module "node:path" {
  export function join(...parts: string[]): string
  export function dirname(path: string): string
}
