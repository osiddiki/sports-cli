---------------------------- MODULE RelayFailover ----------------------------
(* The merged live relay in sportscli/relay.py (class Merged) and the output
   timeline in sportscli/merge.py (class Timeline), reduced to the order in
   which moments of one broadcast reach the player.

   Two sources carry the same broadcast; the backup may trail by a segment.
   A "moment" is one segment's worth of broadcast time. The relay downloads a
   segment before listing it, so the player only ever receives committed
   moments, in commit order.

   Code map:
     Publish        a source's playlist gains its next live segment
     Fail, Recover  Merged.healthy() turning false or true for a source, or Merged.steer()
                    finding the active source slower than real time ("falling behind")
     Switch         Merged.switch(): the active source is unhealthy, the other is healthy
     Return         Merged.steer(): back to the chosen source once it is steady, only from a
                    backup whose timestamps matched it (Merged.exact)
     PumpSame       Timeline.plan() continuing by cursor on the source last used
     PumpResume     Timeline.plan() + Merged.align() after a switch: by segment name,
                    program date, transport-stream timestamp (merge.realign), or,
                    with none of those, by distance from the live edge
     Skip           Merged.pump() skipping a segment that failed on every source
     Start          Timeline.plan() with nothing committed: start near the live edge *)
EXTENDS Naturals

CONSTANTS
    Moments,      \* how much of the broadcast the model explores
    BackupLag,    \* how many segments the backup trails the main source
    Resume,       \* "names", "pdt", "pts" or "edge": what the two sources share
    BuggyResume   \* TRUE models resuming at the last segment's own name (a repeat)

ASSUME Resume \in {"names", "pdt", "pts", "edge"} /\ BuggyResume \in BOOLEAN

Sources == {"main", "backup"}
Lag(s) == IF s = "backup" THEN BackupLag ELSE 0

VARIABLES
    edge,      \* newest moment the broadcast has reached
    healthy,   \* source -> whether its playlist is advancing
    active,    \* the source the relay is pumping from
    last,      \* last moment committed to the player (0 = none yet)
    lastFrom,  \* source of that commit, or "none"
    skipped,   \* TRUE when the next commit follows a skipped segment
    repeat,    \* TRUE once the player received a moment it already had, or an earlier one
    jump       \* TRUE once the player skipped ahead without a skipped segment

vars == <<edge, healthy, active, last, lastFrom, skipped, repeat, jump>>

Published(s) == IF edge > Lag(s) THEN edge - Lag(s) ELSE 0

Init ==
    /\ edge = 1
    /\ healthy = [s \in Sources |-> TRUE]
    /\ active = "main"
    /\ last = 0 /\ lastFrom = "none"
    /\ skipped = FALSE /\ repeat = FALSE /\ jump = FALSE

Commit(m) ==
    /\ repeat' = (repeat \/ (last > 0 /\ m <= last))
    /\ jump' = (jump \/ (last > 0 /\ m > last + 1 /\ ~skipped))
    /\ last' = m /\ lastFrom' = active /\ skipped' = FALSE

Publish ==
    /\ edge < Moments
    /\ edge' = edge + 1
    /\ UNCHANGED <<healthy, active, last, lastFrom, skipped, repeat, jump>>

Fail(s) ==
    /\ healthy[s]
    /\ healthy' = [healthy EXCEPT ![s] = FALSE]
    /\ UNCHANGED <<edge, active, last, lastFrom, skipped, repeat, jump>>

Recover(s) ==
    /\ ~healthy[s]
    /\ healthy' = [healthy EXCEPT ![s] = TRUE]
    /\ UNCHANGED <<edge, active, last, lastFrom, skipped, repeat, jump>>

Switch ==
    /\ ~healthy[active]
    /\ \E s \in Sources \ {active} : healthy[s] /\ active' = s
    /\ UNCHANGED <<edge, healthy, last, lastFrom, skipped, repeat, jump>>

\* Back to the chosen source while the backup still works. The relay only does this
\* from a backup that Merged.align() found on the same clock, which is the "pts" case.
Return ==
    /\ Resume = "pts" /\ active = "backup" /\ healthy["main"]
    /\ active' = "main"
    /\ UNCHANGED <<edge, healthy, last, lastFrom, skipped, repeat, jump>>

\* Nothing committed yet: start up to three segments behind the live edge.
Start ==
    /\ last = 0 /\ healthy[active] /\ Published(active) > 0
    /\ \E m \in 1..Published(active) :
         /\ m >= Published(active) - 2
         /\ Commit(m)
    /\ UNCHANGED <<edge, healthy, active>>

\* Same source as the last commit: the next segment after the cursor.
PumpSame ==
    /\ last > 0 /\ lastFrom = active /\ healthy[active]
    /\ last + 1 <= Published(active)
    /\ Commit(last + 1)
    /\ UNCHANGED <<edge, healthy, active>>

\* First commit from a source after a switch, as Timeline.plan() chooses it:
\*  - "names": the segment after the last one's name, if the new source lists that
\*    name (waiting while the next is unpublished); otherwise the fallbacks below.
\*  - "pdt": the first segment at or after the last one's end, if published;
\*    otherwise the live-edge fallback (it does not wait).
\*  - "edge": keep the same distance from the live edge, which can land early or late.
\*  - "pts": whatever the plan, Merged.align() realigns on transport-stream
\*    timestamps to the moment after the last one, and waits until it is published.
EdgePick(m) == m >= Published(active) - 1

Resumes(m) ==
    CASE Resume = "pts"   -> m = last + 1
      [] Resume = "names" -> IF BuggyResume THEN m = last
                             ELSE IF last <= Published(active) THEN m = last + 1 ELSE EdgePick(m)
      [] Resume = "pdt"   -> IF last + 1 <= Published(active) THEN m = last + 1 ELSE EdgePick(m)
      [] Resume = "edge"  -> EdgePick(m)

PumpResume ==
    /\ last > 0 /\ lastFrom # active /\ healthy[active]
    /\ \E m \in 1..Published(active) : Resumes(m) /\ Commit(m)
    /\ UNCHANGED <<edge, healthy, active>>

\* A segment that failed on every source is passed over; the gap is announced
\* with a discontinuity rather than filled.
Skip ==
    /\ last > 0 /\ healthy[active] /\ last + 1 <= Published(active)
    /\ last' = last + 1 /\ lastFrom' = active /\ skipped' = TRUE
    /\ UNCHANGED <<edge, healthy, active, repeat, jump>>

Next ==
    \/ Publish
    \/ \E s \in Sources : Fail(s) \/ Recover(s)
    \/ Switch \/ Return \/ Start \/ PumpSame \/ PumpResume \/ Skip

Spec == Init /\ [][Next]_vars

\* Rules
NoRepeat == ~repeat      \* the player never gets a moment again, or an earlier one
NoJump == ~jump          \* it never skips ahead unless a segment was skipped
OnlyPublished == last <= edge   \* it only gets moments that were published
=============================================================================
