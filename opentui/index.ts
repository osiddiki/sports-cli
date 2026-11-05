// OpenTUI prototype of the Sports CLI games screen. The Python app does the work behind
// `sports-cli --web`; this file only draws the screen and calls that server's JSON API.
import os from "node:os"
import path from "node:path"
import type { Socket, Subprocess } from "bun"
import QRCode from "qrcode"
import {
  BoxRenderable,
  SelectRenderable,
  SelectRenderableEvents,
  TabSelectRenderable,
  TabSelectRenderableEvents,
  TextRenderable,
  createCliRenderer,
  bold,
  dim,
  fg,
  t,
  type SelectOption,
} from "@opentui/core"

const REPO = path.resolve(import.meta.dir, "..")
const C = {
  bg: "#0b0d10", panel: "#12161c", line: "#26303b", focus: "#4f9dff", text: "#d8dee6",
  muted: "#7d8794", live: "#ff5a5f", ok: "#3fcf8e", warn: "#f2b84b", pick: "#1d3557",
}

type Game = { id: string; title: string; state: string; start: string; tv: string }
type Channel = { name: string; featured: boolean; count: number }
type Source = { i: number; name: string; side: string; quality: string; status: string; detail: string; failed: boolean; hidden: boolean; ok: boolean }
type Target = { league: string; game: string } | { channel: string }

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
// A Wi-Fi address works for any phone at home; Tailscale names only for phones on Tailscale.
const homeLink = phoneLinks.find((link) => /\/\/(192\.168|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(link)) ?? phoneLinks[0]!
const base = `http://127.0.0.1:${port}/${token}/`

async function api<T>(route: string, query: Record<string, string> = {}): Promise<T> {
  const response = await fetch(base + route + "?" + new URLSearchParams(query))
  const body = await response.json() as T & { error?: string }
  if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`)
  return body
}

// Screen

const renderer = await createCliRenderer({ exitOnCtrlC: true, backgroundColor: C.bg, onDestroy: () => { stopPlayer(); server.kill() } })

const app = new BoxRenderable(renderer, { id: "app", flexDirection: "column", width: "100%", height: "100%", padding: 1, gap: 1 })
const header = new BoxRenderable(renderer, { id: "header", flexDirection: "row", height: 1, justifyContent: "space-between" })
const brand = new TextRenderable(renderer, { id: "brand", content: t`${bold(fg(C.focus)("● SPORTS"))}${fg(C.muted)("  live games, sources and TV")}` })
const clock = new TextRenderable(renderer, { id: "clock", content: "" })
const phone = new TextRenderable(renderer, { id: "phone", content: t`${fg(C.muted)("phone ")}${fg(C.text)(homeLink)}${fg(C.muted)("  p QR code   ")}` })
const headerRight = new BoxRenderable(renderer, { id: "header-right", flexDirection: "row" })
headerRight.add(phone)
headerRight.add(clock)
header.add(brand)
header.add(headerRight)

const tabs = new TabSelectRenderable(renderer, {
  id: "tabs", width: "100%", tabWidth: 12, showDescription: false, showUnderline: false, showScrollArrows: true, wrapSelection: true,
  backgroundColor: C.bg, textColor: C.muted, focusedBackgroundColor: C.bg, focusedTextColor: C.muted,
  selectedBackgroundColor: C.focus, selectedTextColor: "#ffffff",
})

const body = new BoxRenderable(renderer, { id: "body", flexDirection: "row", flexGrow: 1, gap: 2 })
const panel = (id: string, title: string) => new BoxRenderable(renderer, {
  id, title: ` ${title} `, border: true, borderStyle: "rounded", borderColor: C.line, focusedBorderColor: C.focus,
  titleColor: C.text, backgroundColor: C.panel, flexGrow: 1, flexBasis: 0, padding: 1, flexDirection: "column",
})
const list = (id: string) => new SelectRenderable(renderer, {
  id, flexGrow: 1, width: "100%", showScrollIndicator: true, wrapSelection: false, itemSpacing: 1,
  backgroundColor: C.panel, focusedBackgroundColor: C.panel, textColor: C.text, focusedTextColor: C.text,
  selectedBackgroundColor: C.pick, selectedTextColor: "#ffffff", descriptionColor: C.muted, selectedDescriptionColor: "#b9c7d8",
})
const gamesPanel = panel("games-panel", "Games")
const games = list("games")
gamesPanel.add(games)
const sourcesPanel = panel("sources-panel", "Sources")
const sourcesNote = new TextRenderable(renderer, { id: "sources-note", content: t`${fg(C.muted)("Press Enter on a game to list its sources.")}` })
const sources = list("sources")
sourcesPanel.add(sourcesNote)
sourcesPanel.add(sources)
body.add(gamesPanel)
body.add(sourcesPanel)

const nowPanel = new BoxRenderable(renderer, {
  id: "now-panel", title: " Now playing ", border: true, borderStyle: "rounded", borderColor: C.line, titleColor: C.text,
  backgroundColor: C.panel, height: 3, paddingX: 1,
})
const now = new TextRenderable(renderer, { id: "now", content: t`${fg(C.muted)("Nothing playing")}` })
nowPanel.add(now)
const status = new TextRenderable(renderer, { id: "status", content: "" })
const keys = new TextRenderable(renderer, {
  id: "keys",
  content: t`${bold("←/→")}${dim(" league   ")}${bold("↑/↓")}${dim(" move   ")}${bold("Enter")}${dim(" open/play   ")}${bold("Tab")}${dim(" switch panel   ")}${bold("r")}${dim(" refresh   ")}${bold("p")}${dim(" phone   ")}${bold("q")}${dim(" quit")}`,
})
for (const part of [header, tabs, body, nowPanel, status, keys]) app.add(part)
renderer.root.add(app)

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
const phonePanel = new BoxRenderable(renderer, {
  id: "phone-panel", title: " Open on your phone ", border: true, borderStyle: "rounded", borderColor: C.focus, titleColor: C.text,
  backgroundColor: C.bg, position: "absolute", top: 2, left: 4, zIndex: 10, padding: 1, flexDirection: "column", gap: 1, visible: false,
})
const phoneCode = new TextRenderable(renderer, { id: "phone-code", content: "", fg: "#ffffff", bg: C.bg })
const phoneText = new TextRenderable(renderer, { id: "phone-text", content: "" })
phonePanel.add(phoneCode)
phonePanel.add(phoneText)
renderer.root.add(phonePanel)
let phoneIndex = Math.max(0, phoneLinks.indexOf(homeLink))
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

function say(text: string, color = C.muted) { status.content = t`${fg(color)(text)}` }
setInterval(() => { clock.content = t`${fg(C.muted)(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }))}` }, 1000)

// State. Each load gets a number so a slow answer for a screen the user already left is dropped.

let tab = "nba"
let tabCount = 1
let heading = ""
let target: Target | null = null
let generation = 0
let poll: ReturnType<typeof setTimeout> | undefined

function focus(which: "games" | "sources") {
  const [on, off] = which === "games" ? [games, sources] : [sources, games]
  on.focus(); off.blur()
  gamesPanel.borderColor = which === "games" ? C.focus : C.line
  sourcesPanel.borderColor = which === "sources" ? C.focus : C.line
}

function gameRow(game: Game): SelectOption {
  const start = new Date(game.start)
  const when = game.state === "live" ? "● LIVE" : start.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
  return { name: `${when.padEnd(9)}${game.title}`, description: `${" ".repeat(9)}${game.tv || "No TV listing"}`, value: game }
}

async function loadTab() {
  const mine = ++generation
  clearTimeout(poll)
  target = null
  sources.options = []
  sourcesNote.content = t`${fg(C.muted)(`Press Enter on a ${tab === "tv" ? "channel" : "game"} to list its sources.`)}`
  sourcesPanel.title = " Sources "
  games.options = []
  gamesPanel.title = tab === "tv" ? " Channels " : " Games "
  say(tab === "tv" ? "Loading TV channels…" : "Loading today's schedule…")
  try {
    if (tab === "tv") {
      const channels = await api<Channel[]>("api/channels")
      if (mine !== generation) return
      games.options = channels.map((c) => ({ name: (c.featured ? "★ " : "  ") + c.name, description: `  ${c.count} source${c.count === 1 ? "" : "s"}`, value: c }))
      say(`${channels.length} channels`)
    } else {
      const list = await api<Game[]>("api/games", { league: tab })
      if (mine !== generation) return
      list.sort((a, b) => Number(b.state === "live") - Number(a.state === "live") || a.start.localeCompare(b.start))
      games.options = list.map(gameRow)
      const live = list.filter((g) => g.state === "live").length
      say(list.length ? `${list.length} games today, ${live} live` : "No games today in this league")
    }
    focus("games")
  } catch (error) {
    if (mine === generation) say(`Could not load: ${(error as Error).message}`, C.live)
  }
}

const checking = (source: Source) => /^checking/i.test(source.status)

function sourceRow(source: Source): SelectOption {
  const mark = source.ok ? "✓" : source.failed ? "✗" : checking(source) ? "…" : "·"
  const side = source.side && !["unknown", "-"].includes(source.side) ? ` · ${source.side}` : ""
  return { name: `${mark} ${source.name}${side}`, description: `  ${[source.quality, source.status, source.detail].filter((part) => part && part !== "-").join(" · ")}`, value: source }
}

async function loadSources(next: Target, label: string, refresh = false) {
  const mine = ++generation
  clearTimeout(poll)
  target = next
  heading = label
  sourcesPanel.title = ` ${label} `
  sourcesNote.content = t`${fg(C.muted)("Finding sources…")}`
  sources.options = []
  const query = (): Record<string, string> => ("channel" in next ? { channel: next.channel } : { league: next.league, game: next.game })
  const update = async (first: boolean) => {
    try {
      const answer = await api<{ sources: Source[] }>("api/sources", first && refresh ? { ...query(), refresh: "1" } : query())
      if (mine !== generation) return
      const shown = answer.sources.filter((s) => !s.hidden)
      const keep = (sources.getSelectedOption()?.value as Source | undefined)?.i
      sources.options = shown.map(sourceRow)
      const index = shown.findIndex((s) => s.i === keep)
      if (index >= 0) sources.setSelectedIndex(index)
      const pending = shown.filter(checking).length
      const working = shown.filter((s) => s.ok).length
      sourcesNote.content = shown.length
        ? t`${fg(C.ok)(`${working} working`)}${fg(C.muted)(pending ? `  ·  checking ${pending}…` : "  ·  checks done")}`
        : t`${fg(C.warn)("No sources found yet. Press r to look again.")}`
      if (first && shown.length) focus("sources")
      if (pending) poll = setTimeout(() => update(false), 1500)
    } catch (error) {
      if (mine === generation) sourcesNote.content = t`${fg(C.live)(`Could not load sources: ${(error as Error).message}`)}`
    }
  }
  await update(true)
}

// Player. mpv's own status line (dropped frames, cache) has no terminal here, so read the same
// numbers over its IPC socket and draw them in the Now playing panel.

const WATCHED = ["time-pos", "paused-for-cache", "demuxer-cache-duration", "frame-drop-count", "decoder-frame-drop-count",
  "video-params/h", "estimated-vf-fps", "video-bitrate", "hwdec-current", "pause"] as const
type Stats = Partial<Record<(typeof WATCHED)[number], any>>
type Player = { process: Subprocess; socket?: Socket; title: string; stats: Stats; moved: number; lastPos?: number; timer: ReturnType<typeof setInterval> }
let player: Player | null = null

function stopPlayer() {
  if (!player) return
  clearInterval(player.timer)
  player.socket?.end()
  player.process.kill()
  player = null
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
          close() { if (player === current) stopPlayer() },
        },
      })
      WATCHED.forEach((name, id) => current.socket!.write(JSON.stringify({ command: ["observe_property", id + 1, name] }) + "\n"))
      return
    } catch { await Bun.sleep(200) }
  }
}

async function play(source: Source) {
  if (!target) return
  say(`Starting ${source.name}…`, C.warn)
  try {
    const answer = await api<{ url: string; note: string; mpv: string[] }>("api/play", { ...("channel" in target ? { channel: target.channel } : { league: target.league, game: target.game }), i: String(source.i) })
    stopPlayer()
    const socketPath = path.join(os.tmpdir(), `sports-opentui-${process.pid}-${Date.now()}.sock`)
    const process_ = Bun.spawn(["mpv", ...answer.mpv, `--force-media-title=${heading}`, `--input-ipc-server=${socketPath}`, "--", `http://127.0.0.1:${port}${answer.url}`],
      { stdout: "ignore", stderr: "ignore", stdin: "ignore" })
    const current: Player = { process: process_, title: heading, stats: {}, moved: Date.now(), timer: setInterval(() => player === current && drawNow(current), 1000) }
    player = current
    process_.exited.then(() => { if (player === current) stopPlayer() })
    connectPlayer(current, socketPath)
    say(`Playing ${source.name} in mpv${answer.note ? ` · ${answer.note}` : ""}`, C.ok)
  } catch (error) {
    say(`${source.name} failed: ${(error as Error).message}`, C.live)
  }
}

// Events

games.on(SelectRenderableEvents.ITEM_SELECTED, (_index: number, option: SelectOption) => {
  if (tab === "tv") {
    const channel = option.value as Channel
    loadSources({ channel: channel.name }, channel.name)
  } else {
    const game = option.value as Game
    loadSources({ league: tab, game: game.id }, game.title)
  }
})
sources.on(SelectRenderableEvents.ITEM_SELECTED, (_index: number, option: SelectOption) => play(option.value as Source))
tabs.on(TabSelectRenderableEvents.SELECTION_CHANGED, (_index: number, option: { value?: string }) => {
  if (option.value && option.value !== tab) { tab = option.value; loadTab() }
})

renderer.keyInput.on("keypress", (key) => {
  if (key.name === "q") return renderer.destroy()
  if (key.name === "p" || (key.name === "escape" && phonePanel.visible)) {
    phonePanel.visible = key.name === "p" && !phonePanel.visible
    if (phonePanel.visible) drawPhone()
    return
  }
  if (phonePanel.visible) {
    if (key.name === "left" || key.name === "right") {
      phoneIndex = (phoneIndex + (key.name === "left" ? -1 : 1) + phoneLinks.length) % phoneLinks.length
      drawPhone()
    }
    return
  }
  if (key.name === "tab") return focus(sources.focused ? "games" : "sources")
  if (key.name === "escape" && sources.focused) return focus("games")
  if (key.name === "left" || key.name === "right") {
    const index = tabs.getSelectedIndex() + (key.name === "left" ? -1 : 1)
    const count = tabCount
    tabs.setSelectedIndex((index + count) % count)
    return
  }
  if (key.name === "r") {
    if (target && sources.focused) loadSources(target, heading, true)
    else loadTab()
  }
})

const leagues = await api<{ id: string; name: string }[]>("api/leagues")
const short = (name: string) => name.replace(/^Soccer: /, "").replace("NCAA men's basketball", "NCAAM").replace("NCAA women's basketball", "NCAAW").replace("NCAA football", "NCAAF").replace("Champions League", "UCL").replace("Premier League", "EPL")
const tabOptions = [...leagues.map((l) => ({ name: short(l.name), description: l.name, value: l.id })), { name: "TV", description: "Live TV channels", value: "tv" }]
tabCount = tabOptions.length
tabs.setOptions(tabOptions)
loadTab()
