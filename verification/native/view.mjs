// How /statechart draws Client.tla: what you see on screen and in the player.
const playerState = (v) =>
  v.player === "none" ? "No player" : v.player === "failed" ? "Player failed" : "Playing";

const waiting = (v) => (v.inflight > 0 ? "Waiting for the app" : "In step with the app");

export const title = "Native window and the Python app";

export const state = (v) => ({ Screen: waiting(v), Player: playerState(v) });

export const step = (action) =>
  ({
    YouPressAKey: "you press a key",
    TheTimerPolls: "the timer asks for the screen",
    PythonAnswers: "the app answers",
    Answer: "the app answers the oldest request",
    WindowReceives: "the window reads an answer",
    Receive: "the window reads the oldest answer on a connection",
    ASourceResolvesInTheBackground: "a source finishes resolving",
    TheStreamFails: "the stream gives no picture",
    YouStopThePlayer: "you stop the player",
    Done: null,
  })[action] ?? action;
