// Plain-word labels for the BackupChoice chart: one backup, and what it turns out to be.
export const title = "Starting a live stream with a backup";

const late = (v) => v.held ? " (held past the deadline)" : "";

export const state = (v) => {
  if (v.outcome === "refused") return "Primary failed: nothing plays";
  if (v.outcome === "alone") return "Playing the primary alone";
  if (v.outcome === "backed") return `Playing with the backup${late(v)}`;
  const primary = { pending: "Primary loading", ok: "Primary ready", failed: "Primary failed" }[v.primary];
  const backup = { pending: "backup loading", ok: "backup ready", failed: "backup failed" }[v.load[1]];
  const clock = v.primary === "ok" && v.load[1] === "pending" && v.now >= 2 ? ", deadline reached" : "";
  return `${primary}, ${backup}${clock}`;
};

export const group = (v) =>
  v.outcome === "backed" ? "ok" : v.outcome === "alone" ? "degraded" : v.outcome === "refused" ? "down" : "wait";

export const colors = { ok: "teal", degraded: "orange", down: "pink", wait: "blue" };

export const step = (action) => ({
  PrimaryLoads: "the primary's playlist loads",
  PrimaryFails: "the primary's playlist fails",
  BackupLoads: "the backup's playlist loads",
  BackupFails: "the backup's playlist fails",
  Tick: null,
  Decide: "playback starts",
  Done: "nothing more to do",
}[action.replace(/\(.*\)$/, "")] ?? action);
