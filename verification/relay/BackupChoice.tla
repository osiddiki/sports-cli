---------------------------- MODULE BackupChoice ----------------------------
(* How a live stream with several backups starts: sportscli/relay.py, class Merged,
   methods prepare(), best_backup(), shares_timeline() and clock().

   Every source's playlist loads at once. The primary must load. A backup is optional:
   it has until the deadline (Merged.PREPARE_WAIT), counted from the start. Then one
   backup is kept: the first, in the order offered, that lines up with the primary,
   else the first that loaded.

   Code map:
     PrimaryLoads, PrimaryFails   the primary's Merged._try_load() returning
     BackupLoads, BackupFails     a backup's Merged._try_load() returning, before or after the choice
     Tick                         time passing while prepare() waits
     Decide                       prepare() after concurrent.futures.wait() returns, with best_backup()

   How a backup relates to the primary (fixed per run, every combination explored):
     "names"       shares segment names               shares_timeline() is TRUE
     "dates"       program dates within 30 s          shares_timeline() is TRUE
     "apart"       program dates further apart        shares_timeline() is FALSE
     "clock"       timestamps within SAME_CLOCK       shares_timeline() is None, clock() matches
     "other"       timestamps of another encoder      shares_timeline() is None, clock() differs
     "unreadable"  its newest segment cannot be read  shares_timeline() is None, clock() is None *)
EXTENDS Naturals, FiniteSets

CONSTANTS
    Backups,      \* how many backups are offered, numbered 1..Backups in the order offered
    Deadline,     \* ticks a backup's playlist may take
    WaitForAll,   \* TRUE models the old prepare(), which waited for every playlist
    FirstReady    \* TRUE models keeping the first loaded backup without comparing

ASSUME Backups \in Nat /\ Deadline \in Nat /\ WaitForAll \in BOOLEAN /\ FirstReady \in BOOLEAN

B == 1..Backups
Kinds == {"names", "dates", "apart", "clock", "other", "unreadable"}
LinesUp(k) == k \in {"names", "dates", "clock"}

VARIABLES
    primary,   \* "pending", "ok" or "failed"
    load,      \* backup -> "pending", "ok" or "failed"
    kind,      \* backup -> how it relates to the primary
    now,       \* ticks since prepare() started
    offered,   \* the backups that had loaded when the choice was made
    outcome,   \* "undecided", "refused" (primary failed), "alone" (no backup) or "backed"
    kept,      \* the backup kept when outcome is "backed", else 0
    held       \* whether prepare() ever waited past the deadline with the primary ready

vars == <<primary, load, kind, now, offered, outcome, kept, held>>

Min(S) == CHOOSE x \in S : \A y \in S : x <= y
Ready == {b \in B : load[b] = "ok"}
Settled == \A b \in B : load[b] # "pending"

\* Merged.best_backup(): the first that lines up, else the first.
Best(R) == IF \E b \in R : LinesUp(kind[b]) THEN Min({b \in R : LinesUp(kind[b])}) ELSE Min(R)

Init ==
    /\ primary = "pending"
    /\ load = [b \in B |-> "pending"]
    /\ kind \in [B -> Kinds]
    /\ now = 0
    /\ offered = {}
    /\ outcome = "undecided"
    /\ kept = 0
    /\ held = FALSE

PrimaryLoads == primary = "pending" /\ primary' = "ok" /\ UNCHANGED <<load, kind, now, offered, outcome, kept, held>>
PrimaryFails == primary = "pending" /\ primary' = "failed" /\ UNCHANGED <<load, kind, now, offered, outcome, kept, held>>
BackupLoads(b) == load[b] = "pending" /\ load' = [load EXCEPT ![b] = "ok"] /\ UNCHANGED <<primary, kind, now, offered, outcome, kept, held>>
BackupFails(b) == load[b] = "pending" /\ load' = [load EXCEPT ![b] = "failed"] /\ UNCHANGED <<primary, kind, now, offered, outcome, kept, held>>

\* Time passes. Once the primary is ready and the deadline has come, concurrent.futures.wait() has
\* returned, so prepare() decides without waiting any longer.
Waiting == primary = "ok" /\ outcome = "undecided" /\ now >= Deadline
Tick ==
    /\ now <= Deadline
    /\ WaitForAll \/ ~Waiting
    /\ now' = now + 1
    /\ held' = (held \/ Waiting)
    /\ UNCHANGED <<primary, load, kind, offered, outcome, kept>>

Decide ==
    /\ outcome = "undecided"
    /\ primary # "pending"
    /\ primary = "failed" \/ Settled \/ (~WaitForAll /\ now >= Deadline)
    /\ offered' = IF primary = "ok" THEN Ready ELSE {}
    /\ outcome' = IF primary = "failed" THEN "refused" ELSE IF Ready = {} THEN "alone" ELSE "backed"
    /\ kept' = IF primary = "failed" \/ Ready = {} THEN 0
               ELSE IF FirstReady THEN Min(Ready)
               ELSE Best(Ready)
    /\ UNCHANGED <<primary, load, kind, now, held>>

Next ==
    \/ PrimaryLoads \/ PrimaryFails \/ Tick \/ Decide
    \/ \E b \in B : BackupLoads(b) \/ BackupFails(b)

Spec == Init /\ [][Next]_vars

\* A stream never starts on a primary that did not load.
PrimaryRequired == outcome \in {"alone", "backed"} => primary = "ok"

\* A ready primary is never kept waiting past the deadline for a backup that has not answered.
StartsOnTime == ~held

\* The kept backup had loaded when the choice was made; a late one is never used.
KeptWasReady == outcome = "backed" => kept \in offered

\* A loaded backup is used: the stream plays alone only when none had loaded.
UsesABackup == outcome = "alone" => offered = {}

\* When a loaded backup lines up with the primary, one that lines up is kept, the earliest offered.
KeepsTheOneThatLinesUp ==
    (outcome = "backed" /\ \E b \in offered : LinesUp(kind[b])) =>
        /\ LinesUp(kind[kept])
        /\ \A b \in offered : LinesUp(kind[b]) => kept <= b

\* With nothing lining up, the first loaded backup is kept.
ElseTheFirst == (outcome = "backed" /\ ~\E b \in offered : LinesUp(kind[b])) => kept = Min(offered)
=============================================================================
