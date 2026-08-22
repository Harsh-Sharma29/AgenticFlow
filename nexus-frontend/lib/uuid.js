export function generateUUID() {
  if (typeof crypto !== "undefined") {
    if (typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }

    if (typeof crypto.getRandomValues === "function") {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);

      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;

      return [
        [...bytes.slice(0, 4)].map(b => b.toString(16).padStart(2, "0")).join(""),
        [...bytes.slice(4, 6)].map(b => b.toString(16).padStart(2, "0")).join(""),
        [...bytes.slice(6, 8)].map(b => b.toString(16).padStart(2, "0")).join(""),
        [...bytes.slice(8, 10)].map(b => b.toString(16).padStart(2, "0")).join(""),
        [...bytes.slice(10, 16)].map(b => b.toString(16).padStart(2, "0")).join("")
      ].join("-");
    }
  }

  throw new Error("Secure UUID generation is unavailable in this browser");
}
