// OpenTUI front end for Sports CLI. The Python app runs headless behind `sports-cli --web` with the
// same screens, statechart and keys as the curses app (sportscli/remote.py); this file sends key
// names and draws the screen each answer describes, and starts mpv when the app asks for it.
import os from "node:os"
import path from "node:path"
import type { Socket, Subprocess } from "bun"
import QRCode from "qrcode"
import {
  BoxRenderable,
  InputRenderable,
  InputRenderableEvents,
  StyledText,
  TextRenderable,
  createCliRenderer,
  bg,
  bold,
  fg,
  t,
  type KeyEvent,
} from "@opentui/core"

const REPO = path.resolve(import.meta.dir, "..")
const C = {
  bg: "#0b0d10", panel: "#12161c", line: "#26303b", focus: "#4f9dff", text: "#d8dee6",
  muted: "#7d8794", live: "#ff5a5f", ok: "#3fcf8e", warn: "#f2b84b", pick: "#1d3557",
}
const SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

type Row = { text: string; cells: string[]; detail: string; style: string; pick: boolean }
type AppEvent =
  | { type: "play"; title: string; command: string[] }
  | { type: "inspect"; url: string; headers: Record<string, string> }
  | { type: "prompt"; label: string; initial: string; key: string }
  | { type: "quit" }
type View = {
  mode: string; title: string; busy: boolean; message: string; info: string[]; columns: string[] | null
  rows: Row[]; selected: number; empty: string; actions: string; global: string; query: string | null; events: AppEvent[]
}

// Backend

// The phone link keeps working between runs when the port stays the same as `sports --web`'s (the token is saved).
function openPort(port: number) {
  try {
    const probe = Bun.listen({ hostname: "0.0.0.0", port, socket: { data() {} } })
    const open = probe.port
    probe.stop(true)
    return open
  } catch { return undefined }
}

const port = openPort(8765) ?? openPort(0)!
const server = Bun.spawn(["python3", path.join(REPO, "sports-cli"), "--web", "--web-port", String(port)], {
  stdout: "pipe", stderr: "ignore", env: { ...process.env, PYTHONUNBUFFERED: "1" },
})
const phoneLinks: string[] = await (async () => {
  const decoder = new TextDecoder()
  let seen = ""
  for await (const chunk of server.stdout) {
    seen += decoder.decode(chunk)
    if (seen.includes("The link is the password")) return seen.match(/http:\/\/\S+/g) ?? []
  }
  throw new Error("the Sports CLI server stopped before it was ready")
})()
const token = phoneLinks[0]!.match(/:\d+\/([A-Za-z0-9_-]+)\//)![1]!
// The server lists the Tailscale link first: it works at home and away, while the Wi-Fi link needs the same network.
const homeLink = phoneLinks[0]!
const base = `http://127.0.0.1:${port}/${token}/`

async function api<T>(route: string, query: Record<string, string> = {}): Promise<T> {
  const response = await fetch(base + route + "?" + new URLSearchParams(query))
  const body = await response.json() as T & { error?: string }
  if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`)
  return body
}

// Screen

const renderer = await createCliRenderer({ exitOnCtrlC: true, backgroundColor: C.bg, onDestroy: () => { clearInterval(refresh); stopPlayer(); server.kill() } })

const app = new BoxRenderable(renderer, { id: "app", flexDirection: "column", width: "100%", height: "100%", paddingX: 1 })
const header = new BoxRenderable(renderer, { id: "header", flexDirection: "row", height: 1, justifyContent: "space-between" })
const crumb = new TextRenderable(renderer, { id: "crumb", content: "" })
const corner = new TextRenderable(renderer, { id: "corner", content: "" })
header.add(crumb)
header.add(corner)
const info = new TextRenderable(renderer, { id: "info", content: "" })
const listPanel = new BoxRenderable(renderer, {
  id: "list-panel", border: true, borderStyle: "rounded", borderColor: C.focus, titleColor: C.text,
  backgroundColor: C.panel, flexGrow: 1, paddingX: 1, flexDirection: "column",
})
const list = new TextRenderable(renderer, { id: "list", content: "", flexGrow: 1 })
listPanel.add(list)
const nowPanel = new BoxRenderable(renderer, {
  id: "now-panel", title: " Now playing ", border: true, borderStyle: "rounded", borderColor: C.line, titleColor: C.text,
  backgroundColor: C.panel, height: 3, paddingX: 1,
})
const now = new TextRenderable(renderer, { id: "now", content: t`${fg(C.muted)("Nothing playing")}` })
nowPanel.add(now)
const status = new TextRenderable(renderer, { id: "status", content: "", height: 1 })
const keys = new TextRenderable(renderer, { id: "keys", content: "" })
for (const part of [header, info, listPanel, nowPanel, status, keys]) app.add(part)
renderer.root.add(app)

type Chunk = StyledText["chunks"][number]
const plain = (text: string): Chunk[] => t`${text}`.chunks
const styled = (lines: Chunk[][]) => new StyledText(lines.flatMap((line, i) => (i ? [...plain("\n"), ...line] : line)))

function overlay(id: string, title: string) {
  const box = new BoxRenderable(renderer, {
    id, title: ` ${title} `, border: true, borderStyle: "rounded", borderColor: C.focus, titleColor: C.text,
    backgroundColor: C.bg, position: "absolute", top: 2, left: 4, zIndex: 10, padding: 1, flexDirection: "column", gap: 1, visible: false,
  })
  renderer.root.add(box)
  return box
}

// Phone overlay: a QR code drawn with half blocks, light modules as white so phone cameras read it on a dark terminal.
function qrText(link: string) {
  const { modules } = QRCode.create(link, { errorCorrectionLevel: "L" })
  const quiet = 2, size = modules.size + quiet * 2
  const light = (row: number, col: number) => {
    const r = row - quiet, c = col - quiet
    return r < 0 || c < 0 || r >= modules.size || c >= modules.size || !modules.get(r, c)
  }
  const lines: string[] = []
  for (let row = 0; row < size; row += 2) {
    let line = ""
    for (let col = 0; col < size; col++) {
      const top = light(row, col), bottom = row + 1 < size ? light(row + 1, col) : true
      line += top && bottom ? "█" : top ? "▀" : bottom ? "▄" : " "
    }
    lines.push(line)
  }
  return lines.join("\n")
}
const phonePanel = overlay("phone-panel", "Open on your phone")
const phoneCode = new TextRenderable(renderer, { id: "phone-code", content: "", fg: "#ffffff", bg: C.bg })
const phoneText = new TextRenderable(renderer, { id: "phone-text", content: "" })
phonePanel.add(phoneCode)
phonePanel.add(phoneText)
let phoneIndex = 0
function drawPhone() {
  const link = phoneLinks[phoneIndex]!
  const code = qrText(link), rows = code.split("\n")
  phoneCode.content = code
  phoneCode.height = rows.length
  phoneText.height = 4
  phonePanel.width = Math.max(rows[0]!.length, link.length, 64) + 4
  phonePanel.height = rows.length + 4 + 5
  const more = phoneLinks.length > 1 ? `←/→ other links (${phoneIndex + 1} of ${phoneLinks.length})   ` : "Same Wi-Fi as this computer.   "
  phoneText.content = t`${fg(C.muted)("Scan with the camera, or type:")}
${bold(fg(C.text)(link))}
${fg(C.muted)(more + "p or Esc closes")}
${fg(C.muted)("The link is the password; share it only with people you trust.")}`
}

// Text prompt for the keys that ask for one (f filter, u paste URL, add-on and resolver settings).
const promptPanel = overlay("prompt-panel", "Enter text")
const promptLabel = new TextRenderable(renderer, { id: "prompt-label", content: "" })
const promptInput = new InputRenderable(renderer, {
  id: "prompt-input", width: "100%", backgroundColor: C.panel, focusedBackgroundColor: C.panel, textColor: C.text, focusedTextColor: C.text,
})
const promptHint = new TextRenderable(renderer, { id: "prompt-hint", content: t`${fg(C.muted)("Enter accepts   Esc cancels")}` })
promptPanel.add(promptLabel)
promptPanel.add(promptInput)
promptPanel.add(promptHint)
let promptKey = ""
promptInput.on(InputRenderableEvents.ENTER, () => {
  const text = promptInput.value
  closePrompt()
  send(promptKey, text)
})
function openPrompt(event: Extract<AppEvent, { type: "prompt" }>) {
  promptKey = event.key
  promptLabel.content = t`${fg(C.text)(event.label)}`
  promptInput.value = event.initial
  promptPanel.width = Math.min(renderer.width - 8, 90)
  promptPanel.height = 7
  promptPanel.visible = true
  promptInput.focus()
}
function closePrompt() { promptPanel.visible = false; promptInput.blur() }

const inspectPanel = overlay("inspect-panel", "Media URL")
const inspectText = new TextRenderable(renderer, { id: "inspect-text", content: "" })
inspectPanel.add(inspectText)
function openInspect(event: Extract<AppEvent, { type: "inspect" }>) {
  const lines = [plain("May expire; localhost links need this app open."), [bold(fg(C.text)(event.url))],
    ...Object.entries(event.headers).map(([name, value]) => [fg(C.muted)(`${name}: `), fg(C.text)(value)]), [fg(C.muted)("Any key closes this.")]]
  inspectText.content = styled(lines)
  inspectPanel.width = renderer.width - 8
  inspectPanel.height = Math.min(renderer.height - 4, lines.length + 4 + Math.ceil(event.url.length / (renderer.width - 14)))
  inspectPanel.visible = true
}

// Drawing. Python owns the selection; this keeps a scroll offset per screen so the list does not jump.

let view: View | null = null
let top = 0, topMode = ""

function wrap(text: string, width: number) {
  const lines: string[] = []
  for (const hint of text.split("   ")) {
    if (lines.length && lines[lines.length - 1]!.length + 3 + hint.length <= width) lines[lines.length - 1] += "   " + hint
    else lines.push(hint)
  }
  return lines
}

function hints(text: string): Chunk[] {
  return text.split("   ").flatMap((hint, i) => {
    const [key, ...rest] = hint.split(" ")
    const label = rest.join(" ")
    return [...plain(i ? "   " : ""), bold(fg(C.text)(key!)), fg(C.muted)(label ? " " + label : "")]
  })
}

function draw() {
  if (!view) return
  const v = view
  const width = renderer.width - 2
  crumb.content = t`${bold(fg(C.focus)("● SPORTS"))}${fg(C.muted)("  ›  ")}${bold(fg(C.text)(v.title))}${fg(C.focus)(v.busy ? "  " + SPINNER[Math.floor(Date.now() / 100) % SPINNER.length] : "")}`
  const clock = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
  corner.content = width > 60 + v.title.length + homeLink.length ? t`${fg(C.muted)("phone ")}${fg(C.text)(homeLink)}${fg(C.muted)(`  p QR    ${clock}`)}` : t`${fg(C.muted)(`p phone link    ${clock}`)}`

  const infoLines: Chunk[][] = v.query !== null ? [[fg(C.muted)("Search: "), bold(fg(C.text)(v.query)), fg(C.focus)("▏")]] : []
  v.info.forEach((line, i) => infoLines.push([i === 0 ? bold(fg(C.text)(line)) : fg(C.muted)(line)]))
  info.content = styled(infoLines)
  info.height = infoLines.length
  info.visible = infoLines.length > 0

  const actionLines = [...wrap(v.actions, width), ...wrap(v.global + (v.mode === "search" ? "" : "   p phone"), width)]
  keys.content = styled(actionLines.map(hints))
  keys.height = actionLines.length
  status.content = t`${fg(C.muted)(v.message)}`

  listPanel.title = ` ${v.title} `
  const twoLines = v.rows.some((row) => row.detail) && v.mode !== "home"
  const room = Math.max(1, renderer.height - 1 - infoLines.length - 3 - 1 - actionLines.length - 2 - (v.columns ? 1 : 0))
  const count = Math.max(1, Math.floor(room / (twoLines ? 2 : 1)))
  if (topMode !== v.mode) { top = 0; topMode = v.mode }
  const anchor = v.selected >= 0 ? v.selected : 0
  if (anchor < top) top = anchor
  if (anchor >= top + count) top = anchor - count + 1
  top = Math.max(0, Math.min(top, Math.max(0, v.rows.length - count)))

  const cellCount = Math.max(1, ...v.rows.map((row) => row.cells.length), v.columns?.length ?? 0)
  const widths: number[] = []
  for (let c = 0; c < cellCount - 1; c++) {
    const longest = Math.max(v.columns?.[c]?.length ?? 0, ...v.rows.map((row) => (row.cells.length > c + 1 ? row.cells[c]!.length : 0)))
    widths.push(Math.min(longest, c === 0 ? Math.max(10, Math.floor(width * 0.45)) : 16))
  }
  const fit = (text: string, size: number) => (text.length > size ? text.slice(0, Math.max(0, size - 1)) + "…" : text.padEnd(size))
  const lines: Chunk[][] = []
  if (v.columns) lines.push([fg(C.muted)("  " + v.columns.map((name, c) => (c < widths.length ? fit(name, widths[c]!) : name)).join("  "))])
  if (!v.rows.length) lines.push([fg(C.muted)(`${v.busy ? SPINNER[Math.floor(Date.now() / 100) % SPINNER.length] + " " : ""}${v.empty}`)])
  v.rows.slice(top, top + count).forEach((row, i) => {
    const index = top + i, picked = index === v.selected
    const color = row.style === "heading" || row.style === "empty" ? C.muted : row.style === "team" ? C.focus : C.text
    const cells = row.cells.map((cell, c) => {
      const text = (c < widths.length && row.cells.length > 1 ? fit(cell, widths[c]!) : cell) + (c < row.cells.length - 1 ? "  " : "")
      const last = c === row.cells.length - 1 && row.cells.length > 1
      const tone = row.style === "home" && last ? C.muted : row.style === "live" && c === 0 ? C.live : last && row.style === "ok" ? C.ok : last && row.style === "warn" ? C.warn
        : last && row.style === "fail" ? C.live : last && row.style === "busy" ? C.muted : color
      return { text, tone }
    })
    const lastCell = cells[cells.length - 1]!
    const before = 2 + cells.slice(0, -1).reduce((sum, cell) => sum + cell.text.length, 0)
    lastCell.text = fit(lastCell.text.trimEnd(), Math.max(4, width - 5 - before)).trimEnd()
    const used = before + lastCell.text.length
    const pad = " ".repeat(Math.max(0, width - 4 - used))
    const mark = picked ? "› " : "  "
    const strong = row.style === "bold" || row.style === "team" || row.style === "heading" || picked
    const paint = (text: string, tone: string) => {
      let chunk = fg(tone)(text)
      if (strong) chunk = bold(chunk)
      return picked ? bg(C.pick)(chunk) : chunk
    }
    lines.push([paint(mark, C.focus), ...cells.map((cell) => paint(cell.text, cell.tone)), paint(pad, C.text)])
    if (twoLines) lines.push([fg(picked ? "#b9c7d8" : C.muted)("    " + fit(row.detail, Math.max(10, width - 8)).trimEnd())])
  })
  list.content = styled(lines)
}

// Keys go to Python one at a time and in order, so fast typing in search arrives as typed.

let queue: Promise<void> = Promise.resolve()
let inflight = 0

function send(key: string, text?: string) {
  inflight++
  queue = queue.then(async () => {
    try { apply(await api<View>("api/app/key", text === undefined ? { k: key } : { k: key, text })) }
    catch (error) { status.content = t`${fg(C.live)(`Could not reach the app: ${(error as Error).message}`)}` }
    finally { inflight-- }
  })
}

function apply(next: View) {
  view = next
  for (const event of next.events) {
    if (event.type === "quit") return renderer.destroy()
    if (event.type === "prompt") openPrompt(event)
    if (event.type === "inspect") openInspect(event)
    if (event.type === "play") startPlayer(event.title, event.command)
  }
  draw()
}

const refresh = setInterval(async () => {
  if (inflight) return
  try { apply(await api<View>("api/app/view")) } catch {}
}, 150)
renderer.on("resize", draw)

function keyName(key: KeyEvent): string | null {
  if (key.ctrl && /^[a-z]$/.test(key.name)) return `ctrl-${key.name}`
  const named: Record<string, string> = { up: "up", down: "down", left: "left", right: "right", return: "enter", enter: "enter",
    escape: "escape", backspace: "backspace", tab: "tab" }
  if (named[key.name]) return named[key.name]!
  if (key.sequence && key.sequence.length === 1 && key.sequence >= " " && !key.meta) return key.sequence
  return null
}

renderer.keyInput.on("keypress", (key: KeyEvent) => {
  if (promptPanel.visible) {
    if (key.name === "escape") { closePrompt(); status.content = t`${fg(C.muted)("Canceled.")}` }
    return
  }
  if (inspectPanel.visible) { inspectPanel.visible = false; return }
  if (phonePanel.visible) {
    if (key.name === "left" || key.name === "right") {
      phoneIndex = (phoneIndex + (key.name === "left" ? -1 : 1) + phoneLinks.length) % phoneLinks.length
      drawPhone()
    } else if (key.name === "p" || key.name === "escape" || key.name === "q") phonePanel.visible = false
    return
  }
  if (key.name === "p" && view?.mode !== "search" && !key.ctrl) { phonePanel.visible = true; drawPhone(); return }
  const name = keyName(key)
  if (name) send(name)
})

// Player. mpv's own status line (dropped frames, cache) has no terminal here, so read the same
// numbers over its IPC socket and draw them in the Now playing panel.

const WATCHED = ["time-pos", "paused-for-cache", "demuxer-cache-duration", "frame-drop-count", "decoder-frame-drop-count",
  "video-params/h", "estimated-vf-fps", "video-bitrate", "hwdec-current", "pause"] as const
type Stats = Partial<Record<(typeof WATCHED)[number], any>>
type Player = { process: Subprocess; socket?: Socket; title: string; stats: Stats; moved: number; lastPos?: number; timer: ReturnType<typeof setInterval> }
let player: Player | null = null

function stopPlayer() {
  if (!player) return
  const current = player
  player = null
  clearInterval(current.timer)
  current.socket?.end()
  current.process.kill()
  now.content = t`${fg(C.muted)("Nothing playing")}`
}

function drawNow(current: Player) {
  const s = current.stats
  const stalled = (Date.now() - current.moved) / 1000
  const parts = [
    s["video-params/h"] ? `${s["video-params/h"]}p` : "",
    s["estimated-vf-fps"] ? `${Math.round(s["estimated-vf-fps"])} fps` : "",
    s["hwdec-current"] && s["hwdec-current"] !== "no" ? `hw ${s["hwdec-current"]}` : "software decode",
    typeof s["demuxer-cache-duration"] === "number" ? `cache ${s["demuxer-cache-duration"].toFixed(1)} s` : "",
    `dropped ${s["frame-drop-count"] ?? 0} (decoder ${s["decoder-frame-drop-count"] ?? 0})`,
    s["video-bitrate"] ? `${(s["video-bitrate"] / 1e6).toFixed(1)} Mb/s` : "",
  ].filter(Boolean).join("  ·  ")
  const state = s.pause ? fg(C.muted)("⏸ paused") : s["paused-for-cache"] ? fg(C.warn)("◌ buffering")
    : stalled > 10 ? fg(C.live)(`⚠ stalled ${Math.round(stalled)} s, press Enter on a source to restart`) : fg(C.ok)("▶ playing")
  now.content = t`${state}${fg(C.text)(`  ${current.title}  `)}${fg(C.muted)(parts)}`
}

async function connectPlayer(current: Player, socketPath: string) {
  let buffered = ""
  for (let attempt = 0; attempt < 50 && player === current; attempt++) {
    try {
      current.socket = await Bun.connect({
        unix: socketPath,
        socket: {
          data(_socket, data) {
            buffered += data.toString()
            const lines = buffered.split("\n"); buffered = lines.pop() ?? ""
            for (const line of lines) {
              try {
                const event = JSON.parse(line)
                if (event.event === "property-change") {
                  if (event.name === "time-pos" && event.data !== current.lastPos) { current.lastPos = event.data; current.moved = Date.now() }
                  current.stats[event.name as (typeof WATCHED)[number]] = event.data
                }
              } catch {}
            }
          },
        },
      })
      WATCHED.forEach((name, id) => current.socket!.write(JSON.stringify({ command: ["observe_property", id + 1, name] }) + "\n"))
      return
    } catch { await Bun.sleep(200) }
  }
}

function startPlayer(title: string, command: string[]) {
  stopPlayer()
  const socketPath = path.join(os.tmpdir(), `sports-opentui-${process.pid}-${Date.now()}.sock`)
  const [executable, ...args] = command
  const process_ = Bun.spawn([executable!, `--force-media-title=${title}`, `--input-ipc-server=${socketPath}`, ...args],
    { stdout: "ignore", stderr: "ignore", stdin: "ignore" })
  const current: Player = { process: process_, title, stats: {}, moved: Date.now(), timer: setInterval(() => player === current && drawNow(current), 1000) }
  player = current
  // A player this front end stopped is not a failure; one that exits on its own reports its status, as the curses app does.
  process_.exited.then((code) => {
    if (player !== current) return
    stopPlayer()
    api<View>("api/app/exit", { code: String(code ?? 0) }).then(apply).catch(() => {})
  })
  connectPlayer(current, socketPath)
}

apply(await api<View>("api/app/view"))
