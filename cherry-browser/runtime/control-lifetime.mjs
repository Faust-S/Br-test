// SPDX-License-Identifier: AGPL-3.0-only
// Owns the presentation grant, not the browser actions or the Agent's Task authority.
export class ControlLifetime {
  #epoch = 0
  #control
  #closed = false
  #teardown = Promise.resolve()
  #disposePromise

  get enabled() { return this.#control !== undefined }

  async setEnabled(value, create) {
    if (typeof value !== 'boolean') throw new TypeError('invalid_control_state')
    if (value && this.#closed) throw new Error('control_lifetime_closed')
    if (value && this.#control) return true
    const epoch = ++this.#epoch
    const previous = this.#control
    this.#control = undefined
    if (previous) {
      // Revoke synchronously before invoking close; queued and running operations
      // retain their obsolete generation, even if the page is still visible.
      const prior = this.#teardown
      this.#teardown = prior.then(() => previous.close())
      void this.#teardown.catch(() => {})
    }
    await this.#teardown
    if (!value || epoch !== this.#epoch || this.#closed) return false
    const control = create()
    if (!control || typeof control.close !== 'function') throw new TypeError('invalid_browser_control')
    this.#control = control
    return true
  }

  capture() {
    if (!this.#control) throw new Error('browser_control_not_enabled')
    return Object.freeze({ epoch: this.#epoch, control: this.#control })
  }

  isCurrent(ticket) {
    return !!ticket && !!this.#control && ticket.epoch === this.#epoch && ticket.control === this.#control
  }

  dispose() {
    if (this.#disposePromise) return this.#disposePromise
    this.#closed = true
    this.#disposePromise = this.setEnabled(false)
    return this.#disposePromise
  }
}
