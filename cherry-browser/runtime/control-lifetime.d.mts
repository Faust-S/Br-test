export class ControlLifetime<T extends { close(): Promise<void> }> {
  readonly enabled: boolean
  setEnabled(value: boolean, create?: () => T): Promise<boolean>
  capture(): Readonly<{ epoch: number; control: T }>
  isCurrent(ticket: Readonly<{ epoch: number; control: T }>): boolean
  dispose(): Promise<boolean>
}
