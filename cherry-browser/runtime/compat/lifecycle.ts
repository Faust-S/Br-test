// SPDX-License-Identifier: AGPL-3.0-only
// Re-export only the original browser dependency; not Cherry's application container.
export { Emitter, toDisposable, type Disposable, type Event } from '../../vendor/cherry-studio/src/main/core/lifecycle/event'
export { Signal } from '../../vendor/cherry-studio/src/main/core/lifecycle/signal'
