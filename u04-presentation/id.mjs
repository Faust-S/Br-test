// Host-neutral binding IDs: Web Crypto is available in Node 22 and secure Tauri WebViews.
// Never fall back to Math.random or a predictable counter for a live binding.
export function presentationBindingId() {
  const crypto=globalThis.crypto;
  if (typeof crypto?.randomUUID !== "function")
    throw new Error("SECURE_BINDING_ID_UNAVAILABLE");
  return crypto.randomUUID();
}
