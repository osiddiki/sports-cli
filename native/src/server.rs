//! The Python app behind the window: `sports-cli --web` runs the screens and the stream
//! checks, and this file talks to it. One worker thread sends every request, in the order the
//! UI asked, so a screen can never arrive out of order (verification/native/Client.tla).

use serde::Deserialize;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{Receiver, Sender, channel};

#[derive(Deserialize, Clone, Debug, Default)]
pub struct Row {
    pub text: String,
    #[serde(default)]
    pub cells: Vec<String>,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub style: String,
    #[serde(default)]
    pub pick: bool,
}

#[derive(Deserialize, Clone, Debug, Default)]
pub struct View {
    pub mode: String,
    #[serde(default)]
    pub accent: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub busy: bool,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub info: Vec<String>,
    #[serde(default)]
    pub columns: Option<Vec<String>>,
    #[serde(default)]
    pub rows: Vec<Row>,
    #[serde(default)]
    pub selected: i64,
    #[serde(default)]
    pub empty: String,
    #[serde(default)]
    pub actions: String,
    #[serde(default)]
    pub query: Option<String>,
    #[serde(default)]
    pub events: Vec<Event>,
    #[serde(default)]
    pub seq: u64,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Event {
    Play {
        title: String,
        url: String,
        #[serde(default)]
        headers: HashMap<String, String>,
        command: Vec<String>,
    },
    Prompt {
        label: String,
        #[serde(default)]
        initial: String,
        key: String,
    },
    Inspect {
        url: String,
    },
    Quit,
}

pub enum Request {
    View,
    Key(String, Option<String>),
    Exit(i32),
}

pub enum Reply {
    View(Box<View>),
    Error(String),
}

pub struct Server {
    child: Child,
    requests: Sender<Request>,
    pub replies: Receiver<Reply>,
    pub phone: Option<String>,
}

fn repo() -> PathBuf {
    if let Some(root) = std::env::var_os("SPORTS_CLI_ROOT") {
        return root.into();
    }
    let exe = std::env::current_exe().unwrap_or_default();
    exe.ancestors().find(|dir| dir.join("sports-cli").is_file()).map(PathBuf::from).unwrap_or_else(|| ".".into())
}

/// 8765 when free, so the phone link stays the same as `sports --web`'s between runs.
fn port() -> u16 {
    TcpListener::bind(("0.0.0.0", 8765)).or_else(|_| TcpListener::bind(("0.0.0.0", 0))).and_then(|l| l.local_addr()).map(|a| a.port()).unwrap_or(8765)
}

fn encode(value: &str) -> String {
    value.bytes().map(|b| match b {
        b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
        _ => format!("%{b:02X}"),
    }).collect()
}

fn get(port: u16, path: &str) -> Result<View, String> {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).map_err(|e| e.to_string())?;
    write!(stream, "GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").map_err(|e| e.to_string())?;
    let mut body = Vec::new();
    stream.read_to_end(&mut body).map_err(|e| e.to_string())?;
    let split = body.windows(4).position(|w| w == b"\r\n\r\n").ok_or("no response")?;
    let status = String::from_utf8_lossy(&body[..split]);
    let json = &body[split + 4..];
    if !status.starts_with("HTTP/1.0 200") && !status.starts_with("HTTP/1.1 200") {
        let error: Result<HashMap<String, String>, _> = serde_json::from_slice(json);
        return Err(error.ok().and_then(|e| e.get("error").cloned()).unwrap_or_else(|| status.lines().next().unwrap_or("").to_string()));
    }
    serde_json::from_slice(json).map_err(|e| format!("bad screen from the app: {e}"))
}

impl Server {
    pub fn start(wake: rune::Waker) -> Result<Server, String> {
        let port = port();
        let mut child = Command::new("python3")
            .arg(repo().join("sports-cli"))
            .args(["--web", "--web-port", &port.to_string()])
            .env("PYTHONUNBUFFERED", "1")
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("could not start python3: {e}"))?;
        let mut links = Vec::new();
        for line in BufReader::new(child.stdout.take().ok_or("no output")?).lines() {
            let line = line.map_err(|e| e.to_string())?;
            links.extend(line.split_whitespace().filter(|w| w.starts_with("http://")).map(String::from));
            if line.contains("The link is the password") { break }
        }
        let token = links.first().and_then(|l| l.split('/').nth(3)).ok_or("the app stopped before it was ready")?.to_string();
        let (requests, inbox) = channel::<Request>();
        let (outbox, replies) = channel();
        std::thread::Builder::new().name("sports-api".into()).spawn(move || {
            for request in inbox {
                let path = match request {
                    Request::View => format!("/{token}/api/app/view"),
                    Request::Key(key, None) => format!("/{token}/api/app/key?k={}", encode(&key)),
                    Request::Key(key, Some(text)) => format!("/{token}/api/app/key?k={}&text={}", encode(&key), encode(&text)),
                    Request::Exit(code) => format!("/{token}/api/app/exit?code={code}"),
                };
                let reply = get(port, &path).map(|v| Reply::View(Box::new(v))).unwrap_or_else(Reply::Error);
                if outbox.send(reply).is_err() { return }
                wake();
            }
        }).map_err(|e| e.to_string())?;
        Ok(Server { child, requests, replies, phone: links.first().cloned() })
    }

    pub fn send(&self, request: Request) {
        let _ = self.requests.send(request);
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
