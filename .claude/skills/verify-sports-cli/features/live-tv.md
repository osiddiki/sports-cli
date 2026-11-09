# Live TV

Live TV channels (CNN, CNBC, MS NOW, Fox News and about 2,000 more) from iptv-org's free streams and DaddyLive's 24/7 pages, listed with the main news and network channels first.

## Sub-features

- `tv-open` `3` from any screen opens Live TV; Esc returns Home.
- `tv-start` `./sports-cli --tv` starts on Live TV.
- `tv-filter` `f` filters channels by name.
- `tv-search` `/` search finds channels by name, in a TV CHANNELS group.
- `tv-sources` Enter on a channel lists its sources and checks them.

## How to get to it (user POV)

- Press `3` anywhere, or choose Live TV on Home.
- Start with `./sports-cli --tv`.

## Driving it with drive.py

Preconditions: baseline; network without a proxy for the source checks.

- **Open from Home.** `$D 'expect:LIVE|LATER|No games' key:4 'expect:ESPN +\d+ streams?' shot:channels`. Sports channels lead: ESPN, ESPN2, FS1, TNT, NFL RedZone, NFL Network, then CNN, CNBC, MS NOW, Fox News, Fox Business in bold.
- **Back.** Continue with `key:Esc 'expect:LIVE|LATER|No games'`.
- **Search.** `$D 'expect:LIVE|LATER|No games' key:/ type:cnbc 'expect:TV CHANNELS' shot:results key:Enter 'expect:^ Streams' shot:sources`.
- **Start there.** `$D --app-args="--tv" 'expect:ESPN +\d+ streams?' shot:start`.
- **Filter.** `$D --app-args="--tv" 'expect:ESPN +\d+' key:f 'expect:Filter channels' type:weather key:Enter 'expect:filter: weather' shot:filtered`. Only weather channels remain.
- **Sources.** `$D --app-args="--tv" 'expect:ESPN +\d+' key:Down key:Enter 'expect:ESPN2' 'expect:^ Streams' wait:6 shot:sources`. CNBC lists an iptv-org and a DaddyLive source; the STATUS column fills in (`OK` or `HTTP 404`).

## Gotchas

- The channel list loads in the background (spinner beside `Live TV`); expect a CNN row before pressing Enter.
- iptv-org's CNBC stream has returned HTTP 404; its DaddyLive source played. Report what the STATUS column shows.
- Inside a sandbox proxy, DaddyLive's player host is refused; the list still loads.
