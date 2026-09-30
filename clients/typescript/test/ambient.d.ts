declare module "node:test" {
  export function test(name: string, fn: () => void | Promise<void>): void
}

declare module "node:assert/strict" {
  interface Assert {
    equal(actual: unknown, expected: unknown, message?: string): void
    deepEqual(actual: unknown, expected: unknown, message?: string): void
    ok(value: unknown, message?: string): void
    match(actual: string, expected: RegExp, message?: string): void
    rejects(block: () => Promise<unknown>, validate: (error: unknown) => boolean): Promise<void>
    throws(block: () => unknown, validate: (error: unknown) => boolean): void
  }
  const assert: Assert
  export default assert
}
