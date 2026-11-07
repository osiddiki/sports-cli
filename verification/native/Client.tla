---------------------------- MODULE Client ----------------------------
(* The native window (native/src/main.rs, server.rs) talking to the Python app
   (sportscli/remote.py Remote.view and Remote.key) and running its player.

   The Python side numbers each view (seq) and hands over the events made since
   the last view (play, prompt, quit) with it. The window sends keys as you press
   them and asks for the screen every 150 ms while nothing is waiting.

   Channels = 1 is the code: one worker thread sends every request in order
   (server.rs Server::start). Channels = 2 puts polls on their own connection, as
   the OpenTUI front end does. SeqGuard = TRUE is State::apply showing a view only
   when its seq is newer. StopChecks = TRUE is State::stop sending an exit only
   for a player that has not reported one. *)
EXTENDS Naturals, Sequences

CONSTANTS Channels, SeqGuard, StopChecks, MaxKeys, MaxPolls

VARIABLES
    chan,       \* requests in flight on each connection, oldest first
    out,        \* replies in flight on each connection, oldest first
    sseq,       \* Python: views numbered so far
    made,       \* Python: events made so far (each a play)
    given,      \* Python: events already handed out with a view
    shown,      \* window: seq of the view on screen
    shownLog,   \* window: every seq it put on screen, in order
    applied,    \* window: event numbers applied, in order
    inflight,   \* window: requests sent and not answered
    keys, polls,\* bounds on what the person and the timer do
    player,     \* window: "none", "playing" or "failed"
    pid,        \* window: which play the player belongs to
    reported,   \* window: plays whose ending the app heard (or that need none)
    exits       \* app: how many exits it heard for each play

vars == <<chan, out, sseq, made, given, shown, shownLog, applied, inflight, keys, polls, player, pid, reported, exits>>

Plays == 0..MaxKeys

Init ==
    /\ chan = [c \in 1..Channels |-> <<>>]
    /\ out = [c \in 1..Channels |-> <<>>]
    /\ sseq = 0 /\ made = 0 /\ given = 0
    /\ shown = 0 /\ shownLog = <<>> /\ applied = <<>>
    /\ inflight = 0 /\ keys = 0 /\ polls = 0
    /\ player = "none" /\ pid = 0 /\ reported = {} /\ exits = [p \in Plays |-> 0]

\* You press a key; it goes out on the first connection.
YouPressAKey ==
    /\ keys < MaxKeys
    /\ keys' = keys + 1
    /\ chan' = [chan EXCEPT ![1] = Append(@, "key")]
    /\ inflight' = inflight + 1
    /\ UNCHANGED <<out, sseq, made, given, shown, shownLog, applied, polls, player, pid, reported, exits>>

\* The timer asks for the screen, only while nothing is waiting.
TheTimerPolls ==
    /\ polls < MaxPolls /\ inflight = 0
    /\ polls' = polls + 1
    /\ chan' = [chan EXCEPT ![Channels] = Append(@, "view")]
    /\ inflight' = inflight + 1
    /\ UNCHANGED <<out, sseq, made, given, shown, shownLog, applied, keys, player, pid, reported, exits>>

\* Python answers the oldest request on a connection. A key may start a play.
Answer(c, play) ==
    /\ chan[c] # <<>>
    /\ play => Head(chan[c]) = "key" /\ made < MaxKeys
    /\ LET m == IF play /\ made < MaxKeys THEN made + 1 ELSE made IN
       /\ made' = m
       /\ out' = [out EXCEPT ![c] = Append(@, [seq |-> sseq + 1, from |-> given + 1, to |-> m])]
       /\ given' = m
    /\ sseq' = sseq + 1
    /\ chan' = [chan EXCEPT ![c] = Tail(@)]
    /\ UNCHANGED <<shown, shownLog, applied, inflight, keys, polls, player, pid, reported, exits>>

PythonAnswers == \E c \in 1..Channels, play \in BOOLEAN : Answer(c, play)

\* A source chosen earlier finishes resolving on a worker (App.poll_feed_actions) and
\* starts a play; its event waits for the next view, a poll's or a key's.
ASourceResolvesInTheBackground ==
    /\ made < MaxKeys
    /\ made' = made + 1
    /\ UNCHANGED <<chan, out, sseq, given, shown, shownLog, applied, inflight, keys, polls, player, pid, reported, exits>>

\* The window reads the oldest reply on a connection: events first, then the view.
Receive(c) ==
    /\ out[c] # <<>>
    /\ LET r == Head(out[c])
           new == r.to >= r.from
       IN
       /\ applied' = applied \o [i \in 1..(IF new THEN r.to - r.from + 1 ELSE 0) |-> r.from + i - 1]
       \* A new play replaces the old player; the app already moved on, so it hears nothing.
       /\ IF new
          THEN /\ player' = "playing" /\ pid' = r.to
               /\ reported' = reported \cup (IF player # "none" THEN {pid} ELSE {})
          ELSE UNCHANGED <<player, pid, reported>>
       /\ IF ~SeqGuard \/ r.seq > shown
          THEN shown' = r.seq /\ shownLog' = Append(shownLog, r.seq)
          ELSE UNCHANGED <<shown, shownLog>>
    /\ out' = [out EXCEPT ![c] = Tail(@)]
    /\ inflight' = inflight - 1
    /\ UNCHANGED <<chan, sseq, made, given, keys, polls, exits>>

WindowReceives == \E c \in 1..Channels : Receive(c)

\* The stream gives no picture; the window tells the app once.
TheStreamFails ==
    /\ player = "playing"
    /\ player' = "failed"
    /\ IF pid \notin reported
       THEN reported' = reported \cup {pid} /\ exits' = [exits EXCEPT ![pid] = @ + 1]
       ELSE UNCHANGED <<reported, exits>>
    /\ UNCHANGED <<chan, out, sseq, made, given, shown, shownLog, applied, inflight, keys, polls, pid>>

\* You stop the player (Cmd-. or Backspace), or hand it to mpv.
YouStopThePlayer ==
    /\ player # "none"
    /\ player' = "none"
    /\ IF ~StopChecks \/ pid \notin reported
       THEN reported' = reported \cup {pid} /\ exits' = [exits EXCEPT ![pid] = @ + 1]
       ELSE UNCHANGED <<reported, exits>>
    /\ UNCHANGED <<chan, out, sseq, made, given, shown, shownLog, applied, inflight, keys, polls, pid>>

Done ==
    /\ keys = MaxKeys /\ inflight = 0 /\ player = "none"
    /\ UNCHANGED vars

Next == YouPressAKey \/ TheTimerPolls \/ PythonAnswers \/ ASourceResolvesInTheBackground \/ WindowReceives \/ TheStreamFails \/ YouStopThePlayer \/ Done

Spec == Init /\ [][Next]_vars

\* The screen never goes back to an older view.
ScreenNeverGoesBack == \A i \in 1..Len(shownLog) - 1 : shownLog[i] < shownLog[i + 1]

\* Every event is applied once, in the order Python made it.
EventsInOrder == \A i \in 1..Len(applied) : applied[i] = i

\* The app hears at most one ending for each play, so a source is hidden at most once.
ExitOnce == \A p \in Plays : exits[p] <= 1

\* A play you replaced was never reported as failed or stopped.
ReplacedPlaysStayQuiet == \A p \in Plays : (p \in reported /\ p # pid) => exits[p] <= 1
=============================================================================
