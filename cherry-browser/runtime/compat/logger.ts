// SPDX-License-Identifier: AGPL-3.0-only
// Do not send page URLs, tokens, input text or exception bodies to the MCP stdout.
const silent = (..._values: unknown[]) => {}
const logger = { debug: silent, info: silent, warn: silent, error: silent, verbose: silent, trace: silent }
export const loggerService = { ...logger, withContext: (_scope: string) => logger }
